"""Shared fake pymysql connection for scan-collector tests.

Returns canned rows based on a substring match against the executed SQL, so
tests stay readable and do not break when a query is reformatted. Order
matters: the first registered substring that matches wins, so more specific
patterns must come first.
"""

from __future__ import annotations

from typing import Any

import pymysql

# Sentinel: registering (substring, RAISES_ERROR) simulates a pymysql.Error,
# which is how the collectors' graceful-degradation paths get exercised.
RAISES_ERROR = object()


class ScriptedCursor:
    def __init__(self, conn: ScriptedConnection):
        self._conn = conn
        self._rows: list[dict[str, Any]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._conn.executed.append((sql, params))
        for substring, rows in self._conn.responses:
            if substring in sql:
                if rows is RAISES_ERROR:
                    raise pymysql.err.OperationalError("simulated: object doesn't exist")
                self._rows = rows
                return
        raise AssertionError(f"No canned response registered for query:\n{sql}")

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def __enter__(self) -> ScriptedCursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class ScriptedConnection:
    """responses: ordered list of (substring, rows) — first match wins."""

    def __init__(self, responses: list[tuple[str, Any]]):
        self.responses = responses
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self) -> ScriptedCursor:
        return ScriptedCursor(self)

    def close(self) -> None:
        pass
