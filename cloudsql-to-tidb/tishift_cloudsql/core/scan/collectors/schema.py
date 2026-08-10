"""Schema inventory collector — SKILL.md Phase 2.

Queries information_schema for one business schema (the config's
source.database). All schema-scoped queries are parameterized
(TABLE_SCHEMA = %s); nothing interpolates the schema name into SQL text.

DEFINER is collected on every object type that carries one (routines,
triggers, events, views). On a self-managed MySQL that would be trivia; on
Cloud SQL it is the single most common reason a converted schema fails to
apply, because the definer names a principal (`cloudsqladmin`, an IAM user)
that cannot be created on TiDB. See CSQL-WARNING-2 / CSQL-DDL-1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pymysql

from tishift_cloudsql.models import (
    ColumnInfo,
    ConstraintInfo,
    EventInfo,
    IndexInfo,
    RoutineInfo,
    SchemaInventory,
    TableInfo,
    TriggerInfo,
    ViewInfo,
)

_LEGACY_COLLATION_PREFIXES = ("utf8_", "utf8mb3_")

# DEFINERs that cannot exist on TiDB. `cloudsql*` are Cloud SQL's own
# principals; an '@' in the user part means an IAM principal
# (e.g. `svc@project.iam.gserviceaccount.com`).
_CLOUDSQL_DEFINER_PREFIX = "cloudsql"
_IAM_DEFINER_MARKER = "gserviceaccount.com"


def _query(cursor: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Execute a query and return rows as dicts with lowercase keys.

    information_schema returns UPPERCASE column names regardless of the SELECT
    clause's case; normalize once here so every mapper below can use plain
    lowercase access.

    Parameterless queries pass None (not an empty tuple) so pymysql skips
    %-interpolation entirely — a literal % in such SQL must never be treated as
    a format directive.
    """
    cursor.execute(sql, params if params else None)
    rows = cursor.fetchall()
    return [{k.lower(): v for k, v in row.items()} for row in rows]


def is_foreign_definer(definer: str) -> bool:
    """True when a DEFINER names a principal TiDB cannot have.

    Split on the *last* '@', not the first: an IAM database user is itself
    named like an email address, so MySQL renders its definer as
    `svc@project.iam.gserviceaccount.com@%` — three fields, two separators.
    Splitting on the first '@' would leave the host as
    "project.iam.gserviceaccount.com@%" and the check would silently miss it.

    Deliberately conservative: an ordinary `appuser@%` definer is fine to keep
    as long as that user is recreated on the target, so only Cloud SQL's own
    principals and IAM service accounts are flagged.
    """
    if not definer:
        return False
    user, _, host = definer.rpartition("@")
    user = (user or definer).strip("`'\"").lower()
    host = host.strip("`'\"").lower()
    if user.startswith(_CLOUDSQL_DEFINER_PREFIX):
        return True
    return _IAM_DEFINER_MARKER in user or _IAM_DEFINER_MARKER in host


def _collect_tables(cur: Any, schema: str) -> dict[str, TableInfo]:
    rows = _query(
        cur,
        """
        SELECT t.TABLE_NAME, t.ENGINE, t.TABLE_ROWS, t.DATA_LENGTH, t.INDEX_LENGTH,
               t.TABLE_COLLATION, t.CREATE_OPTIONS, t.AUTO_INCREMENT,
               c.CHARACTER_SET_NAME
        FROM information_schema.TABLES t
        LEFT JOIN information_schema.COLLATIONS c ON c.COLLATION_NAME = t.TABLE_COLLATION
        WHERE t.TABLE_SCHEMA = %s AND t.TABLE_TYPE = 'BASE TABLE'
        ORDER BY t.DATA_LENGTH DESC
        """,
        (schema,),
    )
    tables: dict[str, TableInfo] = {}
    for r in rows:
        tables[r["table_name"]] = TableInfo(
            schema_name=schema,
            table_name=r["table_name"],
            engine=r.get("engine") or "",
            row_estimate=r.get("table_rows") or 0,
            data_bytes=r.get("data_length") or 0,
            index_bytes=r.get("index_length") or 0,
            create_options=r.get("create_options") or "",
            charset=r.get("character_set_name"),
            collation=r.get("table_collation"),
            auto_increment=r.get("auto_increment"),
        )

    partitions = _query(
        cur,
        """
        SELECT TABLE_NAME, PARTITION_METHOD
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = %s AND PARTITION_NAME IS NOT NULL
        GROUP BY TABLE_NAME, PARTITION_METHOD
        """,
        (schema,),
    )
    for r in partitions:
        table = tables.get(r["table_name"])
        if table is not None:
            table.partition_method = r.get("partition_method")

    return tables


def _collect_columns(cur: Any, schema: str) -> list[ColumnInfo]:
    rows = _query(
        cur,
        """
        SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, COLUMN_TYPE,
               IS_NULLABLE, COLUMN_DEFAULT, CHARACTER_MAXIMUM_LENGTH,
               NUMERIC_PRECISION, NUMERIC_SCALE, CHARACTER_SET_NAME,
               COLLATION_NAME, EXTRA
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """,
        (schema,),
    )
    return [
        ColumnInfo(
            schema_name=schema,
            table_name=r["table_name"],
            column_name=r["column_name"],
            ordinal_position=r["ordinal_position"],
            data_type=(r.get("data_type") or "").lower(),
            column_type=r.get("column_type") or "",
            is_nullable=(r.get("is_nullable") == "YES"),
            column_default=r.get("column_default"),
            character_maximum_length=r.get("character_maximum_length"),
            numeric_precision=r.get("numeric_precision"),
            numeric_scale=r.get("numeric_scale"),
            charset=r.get("character_set_name"),
            collation=r.get("collation_name"),
            extra=r.get("extra") or "",
        )
        for r in rows
    ]


def _collect_indexes(cur: Any, schema: str) -> list[IndexInfo]:
    rows = _query(
        cur,
        """
        SELECT TABLE_NAME, INDEX_NAME, INDEX_TYPE, NON_UNIQUE, COLUMN_NAME, SEQ_IN_INDEX
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = %s
        ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
        """,
        (schema,),
    )
    grouped: dict[tuple[str, str], IndexInfo] = {}
    for r in rows:
        key = (r["table_name"], r["index_name"])
        idx = grouped.get(key)
        if idx is None:
            idx = IndexInfo(
                schema_name=schema,
                table_name=r["table_name"],
                index_name=r["index_name"],
                index_type=r.get("index_type") or "BTREE",
                is_unique=(r.get("non_unique") == 0),
            )
            grouped[key] = idx
        idx.columns.append(r["column_name"])
    return list(grouped.values())


def _collect_constraints(cur: Any, schema: str) -> list[ConstraintInfo]:
    rows = _query(
        cur,
        """
        SELECT DISTINCT tc.TABLE_NAME, tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE,
               kcu.REFERENCED_TABLE_NAME
        FROM information_schema.TABLE_CONSTRAINTS tc
        LEFT JOIN information_schema.KEY_COLUMN_USAGE kcu
          ON kcu.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA
         AND kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
         AND kcu.TABLE_NAME = tc.TABLE_NAME
        WHERE tc.TABLE_SCHEMA = %s
        """,
        (schema,),
    )
    return [
        ConstraintInfo(
            schema_name=schema,
            table_name=r["table_name"],
            constraint_name=r["constraint_name"],
            constraint_type=r["constraint_type"],
            foreign_table=r.get("referenced_table_name"),
        )
        for r in rows
    ]


def _collect_routines(cur: Any, schema: str) -> list[RoutineInfo]:
    rows = _query(
        cur,
        """
        SELECT ROUTINE_NAME, ROUTINE_TYPE, ROUTINE_DEFINITION, DEFINER, IS_DETERMINISTIC
        FROM information_schema.ROUTINES
        WHERE ROUTINE_SCHEMA = %s
        """,
        (schema,),
    )
    return [
        RoutineInfo(
            schema_name=schema,
            routine_name=r["routine_name"],
            kind=r.get("routine_type") or "",
            definition=r.get("routine_definition") or "",
            definer=r.get("definer") or "",
            is_deterministic=(r.get("is_deterministic") == "YES"),
        )
        for r in rows
    ]


def _collect_triggers(cur: Any, schema: str) -> list[TriggerInfo]:
    rows = _query(
        cur,
        """
        SELECT TRIGGER_NAME, EVENT_MANIPULATION, EVENT_OBJECT_TABLE,
               ACTION_TIMING, ACTION_STATEMENT, DEFINER
        FROM information_schema.TRIGGERS
        WHERE TRIGGER_SCHEMA = %s
        """,
        (schema,),
    )
    return [
        TriggerInfo(
            schema_name=schema,
            table_name=r.get("event_object_table") or "",
            trigger_name=r["trigger_name"],
            timing=r.get("action_timing") or "",
            event=r.get("event_manipulation") or "",
            definition=r.get("action_statement") or "",
            definer=r.get("definer") or "",
        )
        for r in rows
    ]


def _collect_events(cur: Any, schema: str) -> list[EventInfo]:
    rows = _query(
        cur,
        """
        SELECT EVENT_NAME, EVENT_DEFINITION, INTERVAL_VALUE, INTERVAL_FIELD,
               EXECUTE_AT, DEFINER
        FROM information_schema.EVENTS
        WHERE EVENT_SCHEMA = %s
        """,
        (schema,),
    )
    events = []
    for r in rows:
        if r.get("interval_value") is not None:
            schedule = f"EVERY {r['interval_value']} {r.get('interval_field') or ''}".strip()
        elif r.get("execute_at") is not None:
            schedule = f"AT {r['execute_at']}"
        else:
            schedule = ""
        events.append(
            EventInfo(
                schema_name=schema,
                event_name=r["event_name"],
                schedule=schedule,
                definition=r.get("event_definition") or "",
                definer=r.get("definer") or "",
            )
        )
    return events


def _collect_views(cur: Any, schema: str) -> list[ViewInfo]:
    """TiDB views are always read-only; IS_UPDATABLE flags which source views
    rely on write-through behaviour that will not carry over (WARNING-9)."""
    rows = _query(
        cur,
        """
        SELECT TABLE_NAME, IS_UPDATABLE, DEFINER
        FROM information_schema.VIEWS
        WHERE TABLE_SCHEMA = %s
        """,
        (schema,),
    )
    return [
        ViewInfo(
            schema_name=schema,
            view_name=r["table_name"],
            is_updatable=(r.get("is_updatable") == "YES"),
            definer=r.get("definer") or "",
        )
        for r in rows
    ]


def collect_schema_inventory(conn: pymysql.Connection, schema: str) -> SchemaInventory:
    """Collect the full schema inventory for one business schema."""
    inv = SchemaInventory()

    with conn.cursor() as cur:
        tables = _collect_tables(cur, schema)
        columns = _collect_columns(cur, schema)
        inv.indexes = _collect_indexes(cur, schema)
        inv.constraints = _collect_constraints(cur, schema)
        inv.routines = _collect_routines(cur, schema)
        inv.triggers = _collect_triggers(cur, schema)
        inv.events = _collect_events(cur, schema)
        inv.views = _collect_views(cur, schema)

    for column in columns:
        table = tables.get(column.table_name)
        if table is not None:
            table.columns.append(column)
        collation = (column.collation or "").lower()
        if collation.startswith(_LEGACY_COLLATION_PREFIXES):
            inv.unsupported_collations.append(
                f"{column.table_name}.{column.column_name}: {column.collation}"
            )

    inv.tables = list(tables.values())
    inv.columns = columns
    inv.non_innodb_tables = [
        t.table_name for t in inv.tables if t.engine and t.engine.lower() != "innodb"
    ]

    # Roll the DEFINER check up once, here, so every rule that needs it reads
    # the same list instead of re-deriving it slightly differently.
    for routine in inv.routines:
        if is_foreign_definer(routine.definer):
            inv.definer_objects.append(f"{routine.kind.lower()} {schema}.{routine.routine_name}")
    for trigger in inv.triggers:
        if is_foreign_definer(trigger.definer):
            inv.definer_objects.append(f"trigger {schema}.{trigger.trigger_name}")
    for event in inv.events:
        if is_foreign_definer(event.definer):
            inv.definer_objects.append(f"event {schema}.{event.event_name}")
    for view in inv.views:
        if is_foreign_definer(view.definer):
            inv.definer_objects.append(f"view {schema}.{view.view_name}")

    return inv
