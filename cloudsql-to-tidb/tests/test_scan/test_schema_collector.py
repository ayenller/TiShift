from __future__ import annotations

from tishift_cloudsql.core.scan.collectors.schema import (
    collect_schema_inventory,
    is_foreign_definer,
)
from tests.test_scan.fake_connection import ScriptedConnection

BASE_RESPONSES: list[tuple[str, object]] = [
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
