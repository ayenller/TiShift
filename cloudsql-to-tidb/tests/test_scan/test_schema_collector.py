from __future__ import annotations

from tishift_cloudsql.core.scan.collectors.schema import (
    collect_schema_inventory,
    find_fks_without_unique_parent_index,
    is_foreign_definer,
)
from tests.test_scan.fake_connection import ScriptedConnection

BASE_RESPONSES: list[tuple[str, object]] = [
    # Registered first: both of these also contain "information_schema.STATISTICS"
    # or KEY_COLUMN_USAGE text that a later, broader entry would otherwise steal.
    (
        "REFERENCED_TABLE_NAME IS NOT NULL",
        [
            {
                "CONSTRAINT_NAME": "fk_c",
                "TABLE_NAME": "orders",
                "REFERENCED_TABLE_NAME": "customers",
                "REFERENCED_COLUMN_NAME": "id",
                "ORDINAL_POSITION": 1,
            }
        ],
    ),
    (
        "NON_UNIQUE = 0",
        [
            {
                "TABLE_NAME": "customers",
                "INDEX_NAME": "PRIMARY",
                "NON_UNIQUE": 0,
                "COLUMN_NAME": "id",
                "SEQ_IN_INDEX": 1,
            }
        ],
    ),
    (
        "information_schema.TABLES t",
        [
            {
                "TABLE_NAME": "orders",
                "ENGINE": "InnoDB",
                "TABLE_ROWS": 100,
                "DATA_LENGTH": 2048,
                "INDEX_LENGTH": 1024,
                "TABLE_COLLATION": "utf8mb4_0900_ai_ci",
                "CREATE_OPTIONS": "",
                "AUTO_INCREMENT": 101,
                "CHARACTER_SET_NAME": "utf8mb4",
            },
            {
                "TABLE_NAME": "access_log",
                "ENGINE": "MyISAM",
                "TABLE_ROWS": 5,
                "DATA_LENGTH": 512,
                "INDEX_LENGTH": 0,
                "TABLE_COLLATION": "utf8mb4_general_ci",
                "CREATE_OPTIONS": "",
                "AUTO_INCREMENT": None,
                "CHARACTER_SET_NAME": "utf8mb4",
            },
        ],
    ),
    ("information_schema.PARTITIONS", [{"TABLE_NAME": "orders", "PARTITION_METHOD": "RANGE"}]),
    (
        "information_schema.COLUMNS",
        [
            {
                "TABLE_NAME": "orders",
                "COLUMN_NAME": "id",
                "ORDINAL_POSITION": 1,
                "DATA_TYPE": "BIGINT",
                "COLUMN_TYPE": "bigint unsigned",
                "IS_NULLABLE": "NO",
                "COLUMN_DEFAULT": None,
                "CHARACTER_MAXIMUM_LENGTH": None,
                "NUMERIC_PRECISION": 20,
                "NUMERIC_SCALE": 0,
                "CHARACTER_SET_NAME": None,
                "COLLATION_NAME": None,
                "EXTRA": "auto_increment",
            },
            {
                "TABLE_NAME": "orders",
                "COLUMN_NAME": "note",
                "ORDINAL_POSITION": 2,
                "DATA_TYPE": "TEXT",
                "COLUMN_TYPE": "text",
                "IS_NULLABLE": "YES",
                "COLUMN_DEFAULT": None,
                "CHARACTER_MAXIMUM_LENGTH": 65535,
                "NUMERIC_PRECISION": None,
                "NUMERIC_SCALE": None,
                "CHARACTER_SET_NAME": "utf8mb3",
                "COLLATION_NAME": "utf8mb3_general_ci",
                "EXTRA": "",
            },
        ],
    ),
    (
        "information_schema.STATISTICS",
        [
            {"TABLE_NAME": "orders", "INDEX_NAME": "PRIMARY", "INDEX_TYPE": "BTREE", "NON_UNIQUE": 0, "COLUMN_NAME": "id", "SEQ_IN_INDEX": 1},
            {"TABLE_NAME": "orders", "INDEX_NAME": "ft_note", "INDEX_TYPE": "FULLTEXT", "NON_UNIQUE": 1, "COLUMN_NAME": "note", "SEQ_IN_INDEX": 1},
        ],
    ),
    (
        "information_schema.TABLE_CONSTRAINTS",
        [
            {
                "TABLE_NAME": "orders",
                "CONSTRAINT_NAME": "fk_c",
                "CONSTRAINT_TYPE": "FOREIGN KEY",
                "REFERENCED_TABLE_NAME": "customers",
            }
        ],
    ),
    (
        "information_schema.ROUTINES",
        [
            {
                "ROUTINE_NAME": "recalc",
                "ROUTINE_TYPE": "PROCEDURE",
                "ROUTINE_DEFINITION": "BEGIN END",
                "DEFINER": "cloudsqlsuperuser@%",
                "IS_DETERMINISTIC": "NO",
            }
        ],
    ),
    (
        "information_schema.TRIGGERS",
        [
            {
                "TRIGGER_NAME": "trg",
                "EVENT_MANIPULATION": "INSERT",
                "EVENT_OBJECT_TABLE": "orders",
                "ACTION_TIMING": "BEFORE",
                "ACTION_STATEMENT": "SET NEW.x = 1",
                "DEFINER": "appuser@%",
            }
        ],
    ),
    (
        "information_schema.EVENTS",
        [
            {
                "EVENT_NAME": "purge",
                "EVENT_DEFINITION": "DELETE FROM t",
                "INTERVAL_VALUE": 1,
                "INTERVAL_FIELD": "DAY",
                "EXECUTE_AT": None,
                "DEFINER": "svc@x.iam.gserviceaccount.com@%",
            }
        ],
    ),
    (
        "information_schema.VIEWS",
        [{"TABLE_NAME": "v_active", "IS_UPDATABLE": "YES", "DEFINER": "cloudsqladmin@localhost"}],
    ),
]


def _inventory(overrides: list[tuple[str, object]] | None = None):
    responses = list(overrides or []) + BASE_RESPONSES
    return collect_schema_inventory(ScriptedConnection(responses), "myapp")


def test_collects_tables_with_sizes() -> None:
    inv = _inventory()
    assert {t.table_name for t in inv.tables} == {"orders", "access_log"}
    orders = next(t for t in inv.tables if t.table_name == "orders")
    assert orders.data_bytes == 2048
    assert orders.auto_increment == 101


def test_attaches_partition_method() -> None:
    orders = next(t for t in _inventory().tables if t.table_name == "orders")
    assert orders.partition_method == "RANGE"


def test_lowercases_information_schema_keys() -> None:
    # information_schema returns UPPERCASE names regardless of the SELECT.
    assert _inventory().columns[0].column_name == "id"


def test_lowercases_data_type() -> None:
    assert _inventory().columns[0].data_type == "bigint"


def test_attaches_columns_to_their_table() -> None:
    orders = next(t for t in _inventory().tables if t.table_name == "orders")
    assert [c.column_name for c in orders.columns] == ["id", "note"]


def test_groups_multi_column_indexes() -> None:
    inv = _inventory()
    assert {i.index_name for i in inv.indexes} == {"PRIMARY", "ft_note"}
    pk = next(i for i in inv.indexes if i.index_name == "PRIMARY")
    assert pk.is_unique and pk.columns == ["id"]


def test_flags_non_innodb_tables() -> None:
    assert _inventory().non_innodb_tables == ["access_log"]


def test_records_legacy_collations() -> None:
    assert _inventory().unsupported_collations == ["orders.note: utf8mb3_general_ci"]


def test_event_schedule_rendered_from_interval() -> None:
    assert _inventory().events[0].schedule == "EVERY 1 DAY"


def test_event_schedule_rendered_from_execute_at() -> None:
    inv = _inventory(
        [
            (
                "information_schema.EVENTS",
                [
                    {
                        "EVENT_NAME": "once",
                        "EVENT_DEFINITION": "",
                        "INTERVAL_VALUE": None,
                        "INTERVAL_FIELD": None,
                        "EXECUTE_AT": "2026-01-01 00:00:00",
                        "DEFINER": "appuser@%",
                    }
                ],
            )
        ]
    )
    assert inv.events[0].schedule == "AT 2026-01-01 00:00:00"


def test_updatable_view_detected() -> None:
    assert _inventory().views[0].is_updatable


def test_collects_foreign_definer_objects_only() -> None:
    # appuser@% is an ordinary principal that can be recreated on TiDB, so the
    # trigger is not flagged; the Cloud SQL and IAM ones are.
    assert set(_inventory().definer_objects) == {
        "procedure myapp.recalc",
        "event myapp.purge",
        "view myapp.v_active",
    }


def test_schema_is_passed_as_a_bind_parameter() -> None:
    conn = ScriptedConnection(list(BASE_RESPONSES))
    collect_schema_inventory(conn, "myapp")
    assert all("myapp" not in sql for sql, _params in conn.executed)
    assert any(params == ("myapp",) for _sql, params in conn.executed)


class TestIsForeignDefiner:
    def test_cloudsql_principals(self) -> None:
        assert is_foreign_definer("cloudsqladmin@localhost")
        assert is_foreign_definer("`cloudsqlsuperuser`@`%`")

    def test_iam_service_account(self) -> None:
        assert is_foreign_definer("svc@project.iam.gserviceaccount.com")

    def test_ordinary_user_is_fine(self) -> None:
        assert not is_foreign_definer("appuser@%")
        assert not is_foreign_definer("`root`@`localhost`")

    def test_empty_definer(self) -> None:
        assert not is_foreign_definer("")


def test_auto_increment_tables_come_from_column_extra_not_table_counter() -> None:
    # Regression, found against a live 250-table Cloud SQL schema:
    # information_schema.TABLES.AUTO_INCREMENT is the NEXT counter value. It is
    # NULL for any table never written to, and it is served from a cache
    # governed by information_schema_stats_expiry — two runs minutes apart gave
    # 129 and 138 where 219 tables actually had an auto-increment column.
    inv = _inventory()
    # access_log has AUTO_INCREMENT=NULL in TABLES but no autoinc column either;
    # orders has EXTRA='auto_increment' on `id` and a counter value.
    assert inv.auto_increment_tables == ["orders"]


def test_auto_increment_detected_when_table_counter_is_null() -> None:
    # The 81-table case from the live run: structurally auto-increment, but the
    # cached counter reads NULL because nothing was ever inserted.
    inv = _inventory(
        [
            (
                "information_schema.TABLES t",
                [
                    {
                        "TABLE_NAME": "never_written",
                        "ENGINE": "InnoDB",
                        "TABLE_ROWS": 0,
                        "DATA_LENGTH": 0,
                        "INDEX_LENGTH": 0,
                        "TABLE_COLLATION": "utf8mb4_general_ci",
                        "CREATE_OPTIONS": "",
                        "AUTO_INCREMENT": None,
                        "CHARACTER_SET_NAME": "utf8mb4",
                    }
                ],
            ),
            (
                "information_schema.COLUMNS",
                [
                    {
                        "TABLE_NAME": "never_written",
                        "COLUMN_NAME": "id",
                        "ORDINAL_POSITION": 1,
                        "DATA_TYPE": "BIGINT",
                        "COLUMN_TYPE": "bigint",
                        "IS_NULLABLE": "NO",
                        "COLUMN_DEFAULT": None,
                        "CHARACTER_MAXIMUM_LENGTH": None,
                        "NUMERIC_PRECISION": 20,
                        "NUMERIC_SCALE": 0,
                        "CHARACTER_SET_NAME": None,
                        "COLLATION_NAME": None,
                        "EXTRA": "auto_increment",
                    }
                ],
            ),
        ]
    )
    assert inv.tables[0].auto_increment is None
    assert inv.auto_increment_tables == ["never_written"]


class TestFksWithoutUniqueParentIndex:
    """Regression from a live 250-table ERP: 3 FKs referenced a parent column
    covered only by a non-unique index. MySQL 8.0.16+ refuses to create these
    (ERROR 6125), so the schema could not be rebuilt from its own dump — the
    apply died at table 57 of 250, on the original file as well as the
    converted one."""

    @staticmethod
    def _fk(constraint, child, parent, cols):
        return [
            {
                "constraint_name": constraint,
                "table_name": child,
                "referenced_table_name": parent,
                "referenced_column_name": c,
                "ordinal_position": i,
            }
            for i, c in enumerate(cols, start=1)
        ]

    @staticmethod
    def _idx(table, index, cols, non_unique=0):
        return [
            {
                "table_name": table,
                "index_name": index,
                "non_unique": non_unique,
                "column_name": c,
                "seq_in_index": i,
            }
            for i, c in enumerate(cols, start=1)
        ]

    def test_composite_pk_does_not_satisfy_a_single_column_fk(self) -> None:
        # The exact live case: PRIMARY KEY (branch_code, debtor_no), FK on
        # branch_code alone. branch_code is not unique by itself.
        offenders = find_fks_without_unique_parent_index(
            self._fk("0_debtor_trans_ibfk_2", "0_debtor_trans", "0_cust_branch", ["branch_code"]),
            self._idx("0_cust_branch", "PRIMARY", ["branch_code", "debtor_no"])
            + self._idx("0_cust_branch", "branch_code", ["branch_code"], non_unique=1),
        )
        assert offenders == ["0_debtor_trans.0_debtor_trans_ibfk_2 -> 0_cust_branch(branch_code)"]

    def test_single_column_pk_satisfies_the_fk(self) -> None:
        offenders = find_fks_without_unique_parent_index(
            self._fk("fk1", "child", "parent", ["id"]),
            self._idx("parent", "PRIMARY", ["id"]),
        )
        assert offenders == []

    def test_composite_fk_matching_the_full_unique_key_is_satisfied(self) -> None:
        offenders = find_fks_without_unique_parent_index(
            self._fk("fk1", "child", "parent", ["a", "b"]),
            self._idx("parent", "PRIMARY", ["a", "b"]),
        )
        assert offenders == []

    def test_fk_shorter_than_the_unique_key_is_not_satisfied(self) -> None:
        # (a, b) is not unique when the key is (a, b, c) — a prefix of a unique
        # key is not itself unique. Getting this backwards makes the whole rule
        # silently pass everything.
        offenders = find_fks_without_unique_parent_index(
            self._fk("fk1", "child", "parent", ["a", "b"]),
            self._idx("parent", "PRIMARY", ["a", "b", "c"]),
        )
        assert offenders == ["child.fk1 -> parent(a, b)"]

    def test_fk_longer_than_the_unique_key_is_satisfied(self) -> None:
        # Referencing (a, b) where (a) alone is unique is fine — redundant, but
        # every referenced row is still uniquely identified.
        offenders = find_fks_without_unique_parent_index(
            self._fk("fk1", "child", "parent", ["a", "b"]),
            self._idx("parent", "PRIMARY", ["a"]),
        )
        assert offenders == []

    def test_fk_column_order_must_match_the_index(self) -> None:
        offenders = find_fks_without_unique_parent_index(
            self._fk("fk1", "child", "parent", ["b", "a"]),
            self._idx("parent", "PRIMARY", ["a", "b"]),
        )
        assert offenders == ["child.fk1 -> parent(b, a)"]

    def test_non_unique_index_never_satisfies(self) -> None:
        offenders = find_fks_without_unique_parent_index(
            self._fk("fk1", "child", "parent", ["id"]),
            self._idx("parent", "idx", ["id"], non_unique=1),
        )
        assert offenders == ["child.fk1 -> parent(id)"]

    def test_a_secondary_unique_key_also_satisfies(self) -> None:
        offenders = find_fks_without_unique_parent_index(
            self._fk("fk1", "child", "parent", ["code"]),
            self._idx("parent", "PRIMARY", ["id"])
            + self._idx("parent", "uk_code", ["code"]),
        )
        assert offenders == []

    def test_no_foreign_keys_at_all(self) -> None:
        assert find_fks_without_unique_parent_index([], []) == []
