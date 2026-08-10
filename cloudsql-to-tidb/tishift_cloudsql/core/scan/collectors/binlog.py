"""Binlog variable collector — one SHOW VARIABLES for the whole precheck.

The query lives in rules/binlog_check.py so the variable list and the rules
that judge it can never drift apart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymysql

from tishift_cloudsql.rules.binlog_check import QUERY


def fetch_binlog_variables(conn: pymysql.Connection) -> dict[str, str]:
    """Return {variable_name: value} for every variable the precheck gates on.

    Variables the server does not expose are simply absent from the result;
    the analyzer renders those as "not reported" rather than assuming a value.
    """
    with conn.cursor() as cur:
        cur.execute(QUERY)
        rows = cur.fetchall()
    return {row["Variable_name"]: row["Value"] for row in rows}
