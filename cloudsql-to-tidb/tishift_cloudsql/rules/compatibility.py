"""Compatibility rule registry — scan Phase 3 (Assess & Score).

Single source of truth for BLOCKER-*/WARNING-*/CSQL-* trigger conditions, kept
in lockstep with references/compatibility-rules.md. Each rule's ``check``
returns a count (0 = not triggered) computed from a CompatibilityContext
bundling everything the scan collectors gathered.

Some conditions need query-log analysis this project does not implement (XA,
UDFs, XML functions, GET_LOCK, SQL_CALC_FOUND_ROWS, SAVEPOINT). Those rules
still exist here and fire correctly once real signals are supplied via
QueryLogSignals; until then they default to "not detected" rather than being
silently omitted, and reports must say so.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tishift_cloudsql.models import (
    BinlogPrecheckResult,
    CloudSQLMetadata,
    QueryLogSignals,
    SchemaInventory,
)

SPATIAL_DATA_TYPES = frozenset(
    {
        "geometry",
        "point",
        "linestring",
        "polygon",
        "multipoint",
        "multilinestring",
        "multipolygon",
        "geometrycollection",
    }
)

# Character sets TiDB supports. Everything else is rejected outright, not
# degraded. gbk is TiDB-specific; the rest overlap with MySQL.
SUPPORTED_CHARSETS = frozenset({"ascii", "latin1", "binary", "utf8", "utf8mb3", "utf8mb4", "gbk"})

# TiDB Cloud only supports lower_case_table_names=2 (case-insensitive
# comparison, original case preserved).
TIDB_LOWER_CASE_TABLE_NAMES = 2

# The strictness modes TiDB enables by default. A source that is *laxer* than
# this is the dangerous direction: rows that insert cleanly on Cloud SQL get
# rejected by TiDB mid-load. A source that is stricter is harmless, so the
# check is one-directional rather than an equality comparison — which also
# stops it firing on cosmetic ordering differences in the sql_mode string.
TIDB_DEFAULT_STRICT_MODES = frozenset(
    {
        "STRICT_TRANS_TABLES",
        "ONLY_FULL_GROUP_BY",
        "NO_ZERO_IN_DATE",
        "NO_ZERO_DATE",
        "ERROR_FOR_DIVISION_BY_ZERO",
    }
)

# Cloud SQL's own bookkeeping principals. Migrating any of them is always wrong.
CLOUDSQL_SYSTEM_USER_PREFIX = "cloudsql"


@dataclass
class CompatibilityContext:
    """Everything a compatibility rule needs.

    Gathered by the scan collectors, plus a few facts (query_log, target_sql_mode,
    network_path_confirmed) that come from elsewhere until dedicated collectors
    exist for them.
    """

    inventory: SchemaInventory
    metadata: CloudSQLMetadata
    binlog: BinlogPrecheckResult
    tier: str = "starter"
    continue_replication_planned: bool = False
    query_log: QueryLogSignals = field(default_factory=QueryLogSignals)


@dataclass(frozen=True)
class CompatibilityRule:
    rule_id: str
    severity: str  # "blocker" | "warning"
    feature: str
    action: str
    check: Callable[[CompatibilityContext], int]  # returns a count; 0 = not triggered


def _distinct_tables_with_column(
    predicate: Callable[[object], bool],
) -> Callable[[CompatibilityContext], int]:
    def check(ctx: CompatibilityContext) -> int:
        return len({c.table_name for c in ctx.inventory.columns if predicate(c)})

    return check


def _spatial_column_check(ctx: CompatibilityContext) -> int:
    return len({c.table_name for c in ctx.inventory.columns if c.data_type in SPATIAL_DATA_TYPES})


def _unsupported_charset_check(ctx: CompatibilityContext) -> int:
    return len(
        {
            c.table_name
            for c in ctx.inventory.columns
            if c.charset and c.charset.lower() not in SUPPORTED_CHARSETS
        }
    )


def _case_insensitive_name_collision_check(ctx: CompatibilityContext) -> int:
    """Count table-name groups that only differ by case.

    Harmless when lower_case_table_names already matches TiDB's required value
    2 — the source itself could not hold two such tables. Only a real blocker
    when the source is more case-sensitive (0 or 1) and genuinely holds
    colliding names that TiDB cannot represent as separate tables.
    """
    if ctx.metadata.lower_case_table_names == TIDB_LOWER_CASE_TABLE_NAMES:
        return 0
    lowered: dict[str, set[str]] = {}
    for t in ctx.inventory.tables:
        lowered.setdefault(t.table_name.lower(), set()).add(t.table_name)
    return sum(1 for names in lowered.values() if len(names) > 1)


def _lax_sql_mode_check(ctx: CompatibilityContext) -> int:
    """Count strictness modes TiDB enables by default that the source does not."""
    if ctx.metadata.sql_mode is None:
        return 0
    source_modes = {m.strip().upper() for m in ctx.metadata.sql_mode.split(",") if m.strip()}
    return len(TIDB_DEFAULT_STRICT_MODES - source_modes)


def _binlog_fail_gate(rule_id: str) -> Callable[[CompatibilityContext], int]:
    def check(ctx: CompatibilityContext) -> int:
        if not ctx.continue_replication_planned:
            return 0
        return sum(1 for c in ctx.binlog.checks if c.rule_id == rule_id and c.status == "fail")

    return check


BLOCKER_RULES: list[CompatibilityRule] = [
    CompatibilityRule(
        rule_id="BLOCKER-1",
        severity="blocker",
        feature="Stored procedures — parsed but cannot execute",
        action="Convert to application code (Python/Go/Java/JS)",
        check=lambda ctx: sum(1 for r in ctx.inventory.routines if r.kind.upper() == "PROCEDURE"),
    ),
    CompatibilityRule(
        rule_id="BLOCKER-2",
        severity="blocker",
        feature="Triggers — parsed but cannot execute",
        action="Move logic to application middleware",
        check=lambda ctx: len(ctx.inventory.triggers),
    ),
    CompatibilityRule(
        rule_id="BLOCKER-3",
        severity="blocker",
        feature="Scheduled events — not supported",
        action="Use cron, Kubernetes CronJob, or Cloud Scheduler + Cloud Run",
        check=lambda ctx: len(ctx.inventory.events),
    ),
    CompatibilityRule(
        rule_id="BLOCKER-4",
        severity="blocker",
        feature="Spatial/GIS columns — data type, functions, and indexes all unsupported",
        action="Convert columns to JSON with COMMENT 'was: <original_type>'",
        check=_spatial_column_check,
    ),
    CompatibilityRule(
        rule_id="BLOCKER-5",
        severity="blocker",
        feature="XA distributed transactions — not supported",
        action="Refactor to single-shard transactions or a saga pattern",
        check=lambda ctx: int(ctx.query_log.xa_detected),
    ),
    CompatibilityRule(
        rule_id="BLOCKER-6",
        severity="blocker",
        feature="User-defined functions — not supported",
        action="Convert to application-layer functions",
        check=lambda ctx: ctx.query_log.udf_count,
    ),
    CompatibilityRule(
        rule_id="BLOCKER-7",
        severity="blocker",
        feature="XML functions (ExtractValue, UpdateXML) — not supported",
        action="Process XML in the application layer",
        check=lambda ctx: int(ctx.query_log.xml_function_detected),
    ),
    CompatibilityRule(
        rule_id="BLOCKER-8",
        severity="blocker",
        feature="Unsupported character set — TiDB only supports ascii/latin1/binary/utf8/utf8mb4/gbk",
        action="Convert affected columns to a supported charset (utf8mb4 by default) before export",
        check=_unsupported_charset_check,
    ),
    CompatibilityRule(
        rule_id="BLOCKER-9",
        severity="blocker",
        feature=(
            "Table names that only differ by case — source is case-sensitive "
            "(lower_case_table_names != 2) but TiDB Cloud only supports 2"
        ),
        action=(
            "Rename one of each colliding pair before migrating. You cannot fix this by "
            "changing the source: MySQL 8.0 forbids changing lower_case_table_names after "
            "initialization, and Cloud SQL does not expose it as a flag"
        ),
        check=_case_insensitive_name_collision_check,
    ),
    CompatibilityRule(
        rule_id="CSQL-BLOCKER-1",
        severity="blocker",
        feature=(
            "Source is a Cloud SQL read replica — Google requires the source of an external "
            "replica to be a primary or standalone instance"
        ),
        action="Point DM at the primary instance instead, or fall back to a cutover-only migration",
        check=lambda ctx: int(ctx.continue_replication_planned and ctx.metadata.is_replica),
    ),
]

WARNING_RULES: list[CompatibilityRule] = [
    CompatibilityRule(
        rule_id="WARNING-2",
        severity="warning",
        feature=(
            "FULLTEXT indexes — real index support is Starter-only (and region-limited); "
            "Essential, Dedicated, and self-hosted only parse the syntax, they don't index"
        ),
        action=(
            "Add a TiFlash replica on the table so columnar scans accelerate scan-based "
            "full-text filtering (LIKE/REGEXP) in place of the index — convert emits this "
            "(CSQL-DDL-5). Rewrite MATCH ... AGAINST queries, or use a dedicated search "
            "engine for relevance ranking"
        ),
        check=lambda ctx: (
            sum(1 for i in ctx.inventory.indexes if i.index_type.upper() == "FULLTEXT")
            if ctx.tier != "starter"
            else 0
        ),
    ),
    CompatibilityRule(
        rule_id="WARNING-3",
        severity="warning",
        feature="AUTO_INCREMENT — unique but NOT sequential",
        action=(
            "Consider AUTO_RANDOM for high-insert tables (convert suggests this, CSQL-DDL-7); "
            "if the application truly needs sequential IDs, TiDB's MySQL Compatibility Mode "
            "allocates them sequentially at a throughput cost"
        ),
        check=lambda ctx: sum(1 for t in ctx.inventory.tables if t.auto_increment is not None),
    ),
    CompatibilityRule(
        rule_id="WARNING-4",
        severity="warning",
        feature="utf8mb4_0900_* collations (MySQL 8 default)",
        action="Maps 1:1 to the same collation on TiDB (supported natively since v7.4) — no action needed",
        check=_distinct_tables_with_column(
            lambda c: (c.collation or "").lower().startswith("utf8mb4_0900")
        ),
    ),
    CompatibilityRule(
        rule_id="WARNING-5",
        severity="warning",
        feature="GET_LOCK/RELEASE_LOCK — limited implementation",
        action="Test advisory locking behaviour; consider Redis-based locks",
        check=lambda ctx: int(ctx.query_log.get_lock_detected),
    ),
    CompatibilityRule(
        rule_id="WARNING-6",
        severity="warning",
        feature="SQL_CALC_FOUND_ROWS — works but triggers a full table scan",
        action="Replace with a separate COUNT(*) query",
        check=lambda ctx: int(ctx.query_log.sql_calc_found_rows_detected),
    ),
    CompatibilityRule(
        rule_id="WARNING-7",
        severity="warning",
        feature="SAVEPOINT — pessimistic mode only",
        action="Ensure pessimistic transaction mode is enabled (default in TiDB)",
        check=lambda ctx: int(ctx.query_log.savepoint_detected),
    ),
    CompatibilityRule(
        rule_id="WARNING-8",
        severity="warning",
        feature="lower_case_table_names mismatch — TiDB Cloud only supports value 2",
        action=(
            "Verify no application code depends on case-sensitive table-name matching; "
            "TiDB always compares table names case-insensitively. Not fixable on the "
            "Cloud SQL side — see BLOCKER-9"
        ),
        check=lambda ctx: int(
            ctx.metadata.lower_case_table_names is not None
            and ctx.metadata.lower_case_table_names != TIDB_LOWER_CASE_TABLE_NAMES
        ),
    ),
    CompatibilityRule(
        rule_id="WARNING-9",
        severity="warning",
        feature="Updatable views (IS_UPDATABLE=YES) — TiDB views are always read-only",
        action="Redirect writes that currently go through the view to the underlying tables",
        check=lambda ctx: sum(1 for v in ctx.inventory.views if v.is_updatable),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-1",
        severity="warning",
        feature="IAM database authentication — no TiDB equivalent",
        action=(
            "Re-provision those principals as password users on TiDB and update every "
            "client's credential source before cutover; IAM tokens will simply stop working"
        ),
        check=lambda ctx: (
            len(ctx.metadata.iam_users)
            if ctx.metadata.iam_users
            else int(ctx.metadata.iam_authentication_enabled)
        ),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-2",
        severity="warning",
        feature="DEFINER clauses naming Cloud SQL or IAM principals",
        action=(
            "Strip the DEFINER clause — the convert phase does this automatically "
            "(CSQL-DDL-1). Left in place, applying the DDL fails because the principal "
            "cannot be created on TiDB"
        ),
        check=lambda ctx: len(ctx.inventory.definer_objects),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-3",
        severity="warning",
        feature="MySQL 5.7 source — TiDB targets 8.0 semantics",
        action=(
            "Review 5.7-only behaviour: latin1 default charset, lax zero-date handling, "
            "ZEROFILL and integer display widths, and utf8mb4_general_ci vs "
            "utf8mb4_0900_ai_ci default collation"
        ),
        check=lambda ctx: int((ctx.metadata.mysql_version or "").startswith("5.7")),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-4",
        severity="warning",
        feature="Source sql_mode is laxer than TiDB's default",
        action=(
            "Rows that insert cleanly on Cloud SQL will be rejected by TiDB mid-load. "
            "Fix the data, or relax the target's sql_mode deliberately — do not discover "
            "this halfway through the import"
        ),
        check=_lax_sql_mode_check,
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-5",
        severity="warning",
        feature="Cloud SQL system artifacts in scope (mysql.heartbeat, cloudsql* users)",
        action=(
            "Exclude the mysql schema from the dump and from DM's block-allow-list. "
            "mysql.heartbeat in particular replicates continuously and will generate "
            "endless no-op changes on the target"
        ),
        check=lambda ctx: int(ctx.metadata.has_heartbeat_table)
        + len(ctx.metadata.cloudsql_system_users),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-6",
        severity="warning",
        feature="Downstream read replicas attached to the source",
        action=(
            "Decide each replica's fate explicitly in the cutover plan — a replica left "
            "running after cutover is a stale read path the application may still be using"
        ),
        check=lambda ctx: ctx.metadata.connected_replica_count,
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-7",
        severity="warning",
        feature="Non-InnoDB storage engines (Cloud SQL permits MyISAM; TiDB has one engine)",
        action=(
            "Convert to InnoDB before export — convert rewrites the clause (CSQL-DDL-2), "
            "but verify the application does not depend on MyISAM's non-transactional "
            "semantics or on its FULLTEXT behaviour"
        ),
        check=lambda ctx: len(ctx.inventory.non_innodb_tables),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-8",
        severity="warning",
        feature="Binary logging disabled — nothing for DM to replicate from",
        action="gcloud sql instances patch <INSTANCE> --enable-bin-log (needs automatic backups on)",
        check=_binlog_fail_gate("CSQL-WARNING-8"),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-9",
        severity="warning",
        feature="Non-ROW binlog format misses edge cases in data changes",
        action=(
            "Not user-configurable on Cloud SQL — it sets ROW itself when binary logging "
            "is on. A non-ROW value means something else is wrong; investigate"
        ),
        check=_binlog_fail_gate("CSQL-WARNING-9"),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-10",
        severity="warning",
        feature="Partial row images are unsafe for conflict resolution",
        action="gcloud sql instances patch <INSTANCE> --database-flags=binlog_row_image=full",
        check=_binlog_fail_gate("CSQL-WARNING-10"),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-11",
        severity="warning",
        feature="Short binlog retention risks DM losing its position during the initial load",
        action=(
            "gcloud sql instances patch <INSTANCE> --retained-transaction-log-days=7 — "
            "the instance setting, not the flag, is what stops Cloud SQL pruning the logs"
        ),
        check=_binlog_fail_gate("CSQL-WARNING-11"),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-12",
        severity="warning",
        feature="binlog_row_value_options = 'PARTIAL_JSON' — DM cannot parse partial-JSON rows",
        action=(
            "Clear the flag by re-issuing --database-flags without it (the argument replaces "
            "the whole list). Left set, JSON columns corrupt silently rather than failing"
        ),
        check=_binlog_fail_gate("CSQL-WARNING-12"),
    ),
    CompatibilityRule(
        rule_id="CSQL-WARNING-13",
        severity="warning",
        feature="DM does not support binlog transaction compression",
        action="Not an exposed Cloud SQL flag and off by default — open a support case if it reads ON",
        check=_binlog_fail_gate("CSQL-WARNING-13"),
    ),
]

ALL_RULES: list[CompatibilityRule] = [*BLOCKER_RULES, *WARNING_RULES]

COMPATIBLE_FEATURES: list[str] = [
    "InnoDB engine (TiDB's only engine — always compatible)",
    "Foreign keys — enforced natively (TiDB v6.6+). TiDB Cloud DM's precheck still "
    "reports FK warnings even when the migration is safe — see the FK checklist in "
    "docs/sync-guide.md before dismissing them",
    "JSON columns (full JSON path support)",
    "ENUM/SET types",
    "utf8mb4 charset and utf8mb4_0900_* collations",
    "Window functions and CTEs",
    "Prepared statements",
    "Pessimistic transactions (default mode)",
    "RANGE/LIST/HASH/KEY partitioning",
    "Online DDL (distributed implementation)",
    "Generated columns (VIRTUAL and STORED)",
    "CHECK constraints",
    "Views (standard SQL views — read-only; see WARNING-9 for updatable-view usage)",
    "GTID-based replication — Cloud SQL enforces GTID, which is what DM prefers",
]
