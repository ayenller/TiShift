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
    schema: str | None = None,
    exclude_schemas: tuple[str, ...] = DEFAULT_EXCLUDED_SCHEMAS,
) -> list[str]:
    """Return "schema.table" for every base table with no PK or unique index.

    Pass *schema* to scope the check to the database being migrated. Leaving it
    None searches the whole instance, which is right when migrating everything
    and wrong — misleadingly so — when migrating one schema off a shared
    instance.

    Keys are lowercased before use: information_schema returns UPPERCASE column
    names regardless of how the SELECT is written, so indexing by the lowercase
    name the query used raises KeyError against a real server.
    """
    params: tuple[str, ...] = exclude_schemas if schema is None else (*exclude_schemas, schema)
    with conn.cursor() as cur:
        cur.execute(build_query(exclude_schemas, scope_to_schema=schema is not None), params)
        rows = cur.fetchall()
    normalized = [{k.lower(): v for k, v in row.items()} for row in rows]
    return [f"{row['table_schema']}.{row['table_name']}" for row in normalized]
