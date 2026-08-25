from __future__ import annotations

from tishift_cloudsql.core.scan.collectors.valid_indexes import fetch_tables_without_valid_index
from tests.test_scan.fake_connection import ScriptedConnection


def test_normalizes_uppercase_information_schema_keys() -> None:
    # Regression: information_schema returns UPPERCASE column names regardless
    # of the SELECT's case. Indexing by the lowercase name raised KeyError
    # against a live server while passing on a lowercase fixture.
    conn = ScriptedConnection([("information_schema.tables", [{"TABLE_SCHEMA": "s", "TABLE_NAME": "t"}])])
    assert fetch_tables_without_valid_index(conn) == ["s.t"]


def test_excluded_schemas_are_bound_parameters() -> None:
    conn = ScriptedConnection([("information_schema.tables", [])])
    fetch_tables_without_valid_index(conn, exclude_schemas=("mysql", "sys"))
    sql, params = conn.executed[0]
    assert params == ("mysql", "sys")
    assert "mysql" not in sql


def test_unscoped_query_is_instance_wide() -> None:
    conn = ScriptedConnection([("information_schema.tables", [])])
    fetch_tables_without_valid_index(conn, exclude_schemas=("mysql",))
    sql, params = conn.executed[0]
    assert "t.table_schema = %s" not in sql
    assert params == ("mysql",)


def test_scoped_query_filters_to_one_schema() -> None:
    # Regression: an unscoped check on a shared instance reported 6 tables for a
    # tenant_poc migration when only 2 were in scope, costing 12 points not 4.
    conn = ScriptedConnection([("information_schema.tables", [])])
    fetch_tables_without_valid_index(conn, "tenant_poc", exclude_schemas=("mysql",))
    sql, params = conn.executed[0]
    assert "t.table_schema = %s" in sql
    assert params == ("mysql", "tenant_poc")
    assert "tenant_poc" not in sql  # bound, never interpolated
