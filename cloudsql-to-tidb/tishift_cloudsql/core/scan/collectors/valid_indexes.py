"""Valid-indexes collector — tables DM cannot replicate deterministically.

Only run when continue replication is planned; a cutover-only migration does
not care whether a table has a unique key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymysql

from tishift_cloudsql.rules.valid_indexes import DEFAULT_EXCLUDED_SCHEMAS, build_query


def fetch_tables_without_valid_index(
    conn: pymysql.Connection,
    exclude_schemas: tuple[str, ...] = DEFAULT_EXCLUDED_SCHEMAS,
) -> list[str]:
    """Return "schema.table" for every base table with no PK or unique index."""
    with conn.cursor() as cur:
        cur.execute(build_query(exclude_schemas), exclude_schemas)
        rows = cur.fetchall()
    return [f"{row['table_schema']}.{row['table_name']}" for row in rows]
