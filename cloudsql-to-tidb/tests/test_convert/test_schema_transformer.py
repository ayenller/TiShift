from __future__ import annotations

from tishift_cloudsql.core.convert.schema_transformer import transform_schema

FULLTEXT_TABLE = "CREATE TABLE articles (id INT, body TEXT, FULLTEXT KEY ft (body));"
PLAIN_TABLE = "CREATE TABLE t (id INT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB;"


def test_tiflash_replica_emitted_for_fulltext_table() -> None:
    result = transform_schema(FULLTEXT_TABLE, tier="essential", tiflash_replicas=2)
    assert result.fulltext_tables == ["articles"]
    assert result.tiflash_statements == ["ALTER TABLE articles SET TIFLASH REPLICA 2;"]
    assert "SET TIFLASH REPLICA 2" in result.sql
    assert "TISHIFT-REVIEW [CSQL-DDL-5]" in result.sql


def test_tiflash_replica_count_is_honoured() -> None:
    result = transform_schema(FULLTEXT_TABLE, tier="dedicated", tiflash_replicas=3)
    assert result.tiflash_statements == ["ALTER TABLE articles SET TIFLASH REPLICA 3;"]


def test_zero_replicas_downgrades_to_info_comment() -> None:
    result = transform_schema(FULLTEXT_TABLE, tier="starter", tiflash_replicas=0)
    assert result.tiflash_statements == []
    assert "TISHIFT-INFO [CSQL-DDL-5]" in result.sql
    assert "to enable later run: ALTER TABLE articles SET TIFLASH REPLICA 0;" in result.sql
    finding = next(f for f in result.findings if f.rule_id == "CSQL-DDL-5")
    assert finding.action_taken == "noted_only"


def test_no_tiflash_for_table_without_fulltext() -> None:
    result = transform_schema(PLAIN_TABLE, tier="essential")
    assert result.tiflash_statements == []
    assert "TIFLASH" not in result.sql.upper()


def test_convert_is_idempotent() -> None:
    first = transform_schema(FULLTEXT_TABLE, tier="essential", tiflash_replicas=2)
    second = transform_schema(first.sql, tier="essential", tiflash_replicas=2)
    assert second.tiflash_statements == []  # not duplicated
    assert second.sql.count("SET TIFLASH REPLICA") == 1
    third = transform_schema(second.sql, tier="essential", tiflash_replicas=2)
    assert third.sql == second.sql


def test_rewrites_are_idempotent() -> None:
    sql = "CREATE TABLE t (c TEXT COLLATE utf8_general_ci) ENGINE=MyISAM DEFAULT CHARSET=utf8;"
    first = transform_schema(sql, tier="essential")
    second = transform_schema(first.sql, tier="essential")
    rewritten = [f for f in second.findings if f.action_taken == "rewritten"]
    assert rewritten == []
    assert second.sql == first.sql


def test_info_comment_is_not_duplicated_on_rerun() -> None:
    first = transform_schema(FULLTEXT_TABLE, tier="starter", tiflash_replicas=0)
    second = transform_schema(first.sql, tier="starter", tiflash_replicas=0)
    assert second.sql.count("TISHIFT-INFO [CSQL-DDL-5]") == 1


def test_spatial_tables_collected_once() -> None:
    sql = (
        "CREATE TABLE geo (id INT, g POINT NOT NULL, SPATIAL KEY sp (g));"
        "CREATE TABLE geo2 (id INT, g POLYGON);"
    )
    result = transform_schema(sql, tier="essential")
    assert result.spatial_tables == ["geo", "geo2"]


def test_parse_error_reported_when_rewrite_breaks_syntax(monkeypatch) -> None:
    import tishift_cloudsql.core.convert.schema_transformer as st

    # Original parses, rewritten does not — the only case that should surface.
    monkeypatch.setattr(st, "_parse_error", lambda sql: "boom" if "InnoDB" in sql else None)
    result = transform_schema("CREATE TABLE t (id INT) ENGINE=MyISAM;", tier="essential")
    assert result.parse_errors and "boom" in result.parse_errors[0]


def test_preexisting_parse_failure_is_not_blamed_on_the_rewrite(monkeypatch) -> None:
    # sqlglot's MySQL dialect has gaps of its own (SRID on a spatial column is
    # one). A statement it could never parse is not evidence of a bad rewrite.
    import tishift_cloudsql.core.convert.schema_transformer as st

    monkeypatch.setattr(st, "_parse_error", lambda sql: "always broken")
    result = transform_schema("CREATE TABLE t (id INT) ENGINE=MyISAM;", tier="essential")
    assert result.parse_errors == []


def test_real_spatial_srid_statement_produces_no_parse_error() -> None:
    # End-to-end version of the above against the actual sqlglot behaviour.
    sql = (
        "CREATE TABLE store_locations (\n"
        "  id INT NOT NULL AUTO_INCREMENT,\n"
        "  coords POINT NOT NULL SRID 4326,\n"
        "  PRIMARY KEY (id),\n"
        "  SPATIAL KEY sp (coords)\n"
        ") ENGINE=InnoDB;"
    )
    assert transform_schema(sql, tier="essential").parse_errors == []


def test_output_preserves_unrelated_text() -> None:
    sql = "-- a leading comment\n" + PLAIN_TABLE + "\n"
    result = transform_schema(sql, tier="essential")
    assert result.sql == sql  # nothing to change, nothing changed
