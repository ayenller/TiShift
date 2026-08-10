"""Valid-indexes precheck — DM needs a PK or unique index on every business
table to apply row changes deterministically.

Single source of truth for the query in SKILL.md Phase 7 and
docs/sync-guide.md § Valid indexes precheck. The schema exclusion list is the
"known so far" set for Cloud SQL, not an exhaustive list for every environment.
"""

from __future__ import annotations

DEFAULT_EXCLUDED_SCHEMAS: tuple[str, ...] = (
    "mysql",
    "performance_schema",
    "information_schema",
    "sys",
)

QUERY_TEMPLATE = """
SELECT
    t.table_name,
    t.table_schema
FROM
    information_schema.tables AS t
WHERE
    (t.table_schema, t.table_name) NOT IN (
        SELECT
            s.table_schema,
            s.table_name
        FROM
            information_schema.statistics AS s
        WHERE
            s.NON_UNIQUE = 0
        GROUP BY
            s.table_schema,
            s.table_name
    )
    AND t.table_schema NOT IN ({placeholders})
    AND t.table_type = 'BASE TABLE'
"""


def build_query(exclude_schemas: tuple[str, ...] = DEFAULT_EXCLUDED_SCHEMAS) -> str:
    """Render QUERY_TEMPLATE with one %s placeholder per excluded schema."""
    placeholders = ", ".join(["%s"] * len(exclude_schemas))
    return QUERY_TEMPLATE.format(placeholders=placeholders)
