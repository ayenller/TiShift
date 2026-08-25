from __future__ import annotations

from tishift_cloudsql.core.convert.ddl_cleaner import (
    clean_statement,
    is_create_table,
    mask_sql,
    normalize_table_name,
    split_statements,
)


def _rule_ids(findings) -> list[str]:
    return [f.rule_id for f in findings]


# --- mask_sql -------------------------------------------------------------


def test_mask_preserves_length() -> None:
    sql = "SELECT 'abc', `col` /* note */ FROM t -- trailing\n"
    assert len(mask_sql(sql)) == len(sql)


def test_mask_blanks_string_interior_but_keeps_quotes() -> None:
    masked = mask_sql("DEFAULT 'MyISAM'")
    assert masked == "DEFAULT 'xxxxxx'"


def test_mask_blanks_block_comments() -> None:
    masked = mask_sql("a /* ENGINE=MyISAM */ b")
    assert "MyISAM" not in masked
    assert masked.startswith("a ") and masked.endswith(" b")


def test_mask_handles_escaped_quote() -> None:
    sql = r"'it\'s'"
    assert len(mask_sql(sql)) == len(sql)


def test_mask_handles_unterminated_comment() -> None:
    assert len(mask_sql("a /* never closed")) == len("a /* never closed")


# --- split_statements -----------------------------------------------------


def test_split_ignores_semicolon_inside_string() -> None:
    parts = split_statements("INSERT INTO t VALUES ('a;b'); SELECT 1;")
    assert len(parts) == 2


def test_split_keeps_trailing_statement_without_semicolon() -> None:
    assert len(split_statements("SELECT 1; SELECT 2")) == 2


def test_split_preserves_all_text() -> None:
    sql = "-- lead\nCREATE TABLE a (id INT);\n\nCREATE TABLE b (id INT);\n"
    assert "".join(split_statements(sql)) == sql


# --- helpers --------------------------------------------------------------


def test_normalize_table_name_strips_backticks_and_case() -> None:
    assert normalize_table_name("`Db` . `Tbl`") == "db.tbl"


def test_is_create_table() -> None:
    assert is_create_table("CREATE TABLE IF NOT EXISTS `t` (id INT)")
    assert not is_create_table("ALTER TABLE t ADD COLUMN c INT")


# --- CSQL-DDL-1: DEFINER --------------------------------------------------


def test_definer_commented_out_on_view() -> None:
    stmt = "CREATE DEFINER=`cloudsqlsuperuser`@`%` VIEW v AS SELECT 1;"
    new, findings, _flags, _table = clean_statement(stmt)
    assert "CSQL-DDL-1" in _rule_ids(findings)
    assert "TISHIFT-REMOVED [CSQL-DDL-1]" in new
    assert "cloudsqlsuperuser" not in new.split("TISHIFT-REMOVED")[0]


def test_definer_matched_on_iam_service_account() -> None:
    stmt = "CREATE DEFINER=`svc@p.iam.gserviceaccount.com`@`%` PROCEDURE p() BEGIN END;"
    _new, findings, _flags, _table = clean_statement(stmt)
    assert "CSQL-DDL-1" in _rule_ids(findings)


def test_definer_inside_string_literal_is_ignored() -> None:
    stmt = "CREATE TABLE t (c VARCHAR(99) DEFAULT 'DEFINER=`a`@`b`', PRIMARY KEY (c));"
    _new, findings, _flags, _table = clean_statement(stmt)
    assert "CSQL-DDL-1" not in _rule_ids(findings)


# --- CSQL-DDL-2: engines --------------------------------------------------


def test_myisam_rewritten_to_innodb() -> None:
    new, findings, _flags, _table = clean_statement("CREATE TABLE t (id INT) ENGINE=MyISAM;")
    assert "CSQL-DDL-2" in _rule_ids(findings)
    assert "ENGINE=InnoDB" in new
    assert "was ENGINE=MyISAM" in new


def test_innodb_is_left_alone() -> None:
    _new, findings, _flags, _table = clean_statement("CREATE TABLE t (id INT) ENGINE=InnoDB;")
    assert "CSQL-DDL-2" not in _rule_ids(findings)


# --- CSQL-DDL-3: storage-only table options -------------------------------


def test_storage_options_commented_out() -> None:
    stmt = "CREATE TABLE t (id INT) ENGINE=InnoDB ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8;"
    new, findings, _flags, _table = clean_statement(stmt)
    assert _rule_ids(findings).count("CSQL-DDL-3") == 2
    # Masking blanks comment interiors, so anything still visible in the masked
    # copy is live SQL rather than preserved-in-a-comment text.
    assert "ROW_FORMAT" not in mask_sql(new)
    assert "KEY_BLOCK_SIZE" not in mask_sql(new)
    assert "ROW_FORMAT=COMPRESSED" in new  # but preserved for audit


# --- CSQL-DDL-4: utf8 widening --------------------------------------------


def test_utf8_charset_widened() -> None:
    new, findings, _flags, _table = clean_statement(
        "CREATE TABLE t (id INT) DEFAULT CHARSET=utf8;"
    )
    assert "CSQL-DDL-4" in _rule_ids(findings)
    assert "DEFAULT CHARSET=utf8mb4" in new


def test_utf8_collation_widened() -> None:
    # Regression: `\b` after utf8 does not fire before `_`, so a naive pattern
    # rewrites utf8_general_ci to itself and convert stops being idempotent.
    new, _findings, _flags, _table = clean_statement(
        "CREATE TABLE t (c TEXT COLLATE utf8_general_ci);"
    )
    assert "COLLATE utf8mb4_general_ci" in new


def test_utf8mb3_widened() -> None:
    new, _findings, _flags, _table = clean_statement(
        "CREATE TABLE t (c TEXT CHARACTER SET utf8mb3 COLLATE utf8mb3_bin);"
    )
    assert "CHARACTER SET utf8mb4" in new
    assert "COLLATE utf8mb4_bin" in new


def test_utf8mb4_is_not_matched() -> None:
    _new, findings, _flags, _table = clean_statement(
        "CREATE TABLE t (id INT) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;"
    )
    assert "CSQL-DDL-4" not in _rule_ids(findings)


def test_review_note_emitted_once_per_statement() -> None:
    new, _findings, _flags, _table = clean_statement(
        "CREATE TABLE t (c TEXT CHARACTER SET utf8 COLLATE utf8_general_ci) "
        "DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;"
    )
    assert new.count("TISHIFT-REVIEW [CSQL-DDL-4]") == 1


# --- CSQL-DDL-5 / 6 / 7 ---------------------------------------------------


def test_fulltext_sets_flag_but_keeps_clause() -> None:
    stmt = "CREATE TABLE t (id INT, b TEXT, FULLTEXT KEY ft (b));"
    new, findings, flags, _table = clean_statement(stmt)
    assert flags.has_fulltext
    assert "FULLTEXT KEY ft (b)" in new  # parse-only on TiDB, harmless to keep
    # The finding is emitted by the transformer, which knows the replica count.
    assert "CSQL-DDL-5" not in _rule_ids(findings)


def test_spatial_index_commented_out_with_review() -> None:
    stmt = "CREATE TABLE t (id INT, g POINT NOT NULL, SPATIAL KEY sp (g));"
    new, findings, flags, _table = clean_statement(stmt)
    assert "CSQL-DDL-6" in _rule_ids(findings)
    assert flags.has_spatial
    assert "TISHIFT-REVIEW [CSQL-DDL-6]" in new


def test_spatial_column_alone_sets_flag() -> None:
    _new, _findings, flags, _table = clean_statement("CREATE TABLE t (id INT, g POLYGON);")
    assert flags.has_spatial


def test_auto_increment_single_column_pk_suggests_auto_random() -> None:
    stmt = "CREATE TABLE t (id BIGINT NOT NULL AUTO_INCREMENT, PRIMARY KEY (id));"
    _new, findings, flags, _table = clean_statement(stmt)
    assert "CSQL-DDL-7" in _rule_ids(findings)
    assert flags.has_auto_increment_pk
    suggestion = next(f.suggestion for f in findings if f.rule_id == "CSQL-DDL-7")
    assert "AUTO_RANDOM" in suggestion


def test_auto_increment_composite_pk_does_not_suggest() -> None:
    # AUTO_RANDOM cannot replace the leading column of a composite key.
    stmt = (
        "CREATE TABLE t (a INT NOT NULL, b INT NOT NULL AUTO_INCREMENT, PRIMARY KEY (a, b));"
    )
    _new, findings, flags, _table = clean_statement(stmt)
    assert "CSQL-DDL-7" not in _rule_ids(findings)
    assert not flags.has_auto_increment_pk


def test_auto_increment_table_option_is_not_a_pk_hit() -> None:
    stmt = "CREATE TABLE t (id INT NOT NULL, PRIMARY KEY (id)) AUTO_INCREMENT=1000;"
    _new, findings, _flags, _table = clean_statement(stmt)
    assert "CSQL-DDL-7" not in _rule_ids(findings)


# --- scope ----------------------------------------------------------------


def test_table_scoped_rule_does_not_fire_on_a_view() -> None:
    # ENGINE=MyISAM here is only ever text inside a SELECT, not a table option.
    stmt = "CREATE VIEW v AS SELECT 'ENGINE=MyISAM' AS note;"
    _new, findings, _flags, _table = clean_statement(stmt)
    assert "CSQL-DDL-2" not in _rule_ids(findings)


def test_clean_statement_returns_raw_table_name() -> None:
    _new, _findings, _flags, table = clean_statement("CREATE TABLE `My Table` (id INT);")
    assert table == "`My Table`"


# --- CSQL-DDL-4 collation pinning (live-migration regression) --------------


def test_bare_charset_gets_an_explicit_collation() -> None:
    # Regression from a real migration: `DEFAULT CHARSET=utf8mb3` with no
    # COLLATE means utf8mb3_general_ci (case-insensitive) on MySQL, but a bare
    # `CHARSET=utf8mb4` inherits TiDB's default utf8mb4_bin (case-SENSITIVE).
    # That flips comparisons and flips what a UNIQUE key rejects.
    new, _f, _fl, _t = clean_statement(
        "CREATE TABLE t (c VARCHAR(60) NOT NULL, UNIQUE KEY c (c)) DEFAULT CHARSET=utf8mb3;"
    )
    assert "DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci" in new


def test_bare_column_charset_gets_an_explicit_collation() -> None:
    new, _f, _fl, _t = clean_statement("CREATE TABLE t (c TEXT CHARACTER SET utf8mb3);")
    assert "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci" in new


def test_explicit_collation_is_preserved_not_doubled() -> None:
    new, _f, _fl, _t = clean_statement(
        "CREATE TABLE t (c TEXT CHARACTER SET utf8mb3 COLLATE utf8mb3_bin);"
    )
    # The source said utf8mb3_bin, so that is what must carry through — the
    # implied-default collation must NOT also be appended.
    live = mask_sql(new)  # comments blanked, so only executable SQL remains
    assert "utf8mb4_bin" in live
    assert "utf8mb4_general_ci" not in live
    assert live.upper().count("COLLATE") == 1


def test_explicit_table_collation_is_preserved() -> None:
    new, _f, _fl, _t = clean_statement(
        "CREATE TABLE t (id INT) DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;"
    )
    assert "utf8mb4_general_ci" in new
    assert "utf8mb3" not in mask_sql(new)  # no live utf8mb3 left


def test_collation_pinning_is_idempotent() -> None:
    from tishift_cloudsql.core.convert.schema_transformer import transform_schema

    sql = "CREATE TABLE t (c VARCHAR(60)) DEFAULT CHARSET=utf8mb3;"
    first = transform_schema(sql, tier="dedicated")
    second = transform_schema(first.sql, tier="dedicated")
    assert second.sql == first.sql
    assert [f for f in second.findings if f.action_taken == "rewritten"] == []
