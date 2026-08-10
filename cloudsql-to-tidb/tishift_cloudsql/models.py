"""Core data models for TiShift Cloud SQL.

Dataclasses and enums only — no database access and no I/O. Collectors build
these, analyzers consume them, and every analyzer test constructs them by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    BLOCKER = "BLOCKER"
    WARNING = "WARNING"
    COMPATIBLE = "COMPATIBLE"


@dataclass
class CloudSQLMetadata:
    """Server- and platform-level metadata collected during scan.

    All fields are optional so the collector degrades gracefully against a
    plain MySQL server (none of the Cloud SQL flags present) — the toolkit
    stays usable against a self-managed source or a local reproduction.

    Binlog settings gated for continue replication (log_bin, binlog_format,
    binlog_row_image, binlog retention, binlog_transaction_compression) are NOT
    duplicated here — see BinlogPrecheckResult and rules/binlog_check.py, the
    single source of truth for those.
    """

    mysql_version: str | None = None
    version_comment: str | None = None  # "(Google)" on Cloud SQL
    is_cloudsql: bool = False  # inferred from version_comment / system users
    # Cloud SQL edition (ENTERPRISE / ENTERPRISE_PLUS) is NOT exposed over the
    # MySQL protocol. It stays None unless the operator supplies it, and reports
    # must say "unknown" rather than assuming ENTERPRISE — the edition decides
    # the maximum transaction-log retention, which CSQL-WARNING-11 depends on.
    edition: str | None = None

    # Server settings that change behaviour on the target
    sql_mode: str | None = None
    lower_case_table_names: int | None = None
    character_set_server: str | None = None
    collation_server: str | None = None
    transaction_isolation: str | None = None
    max_connections: int | None = None
    local_infile: str | None = None
    binlog_row_value_options: str | None = None

    # GTID is enforced ON by Cloud SQL; recorded to explain the
    # enforce_gtid_consistency statement restrictions, not to gate on.
    gtid_mode: str | None = None
    enforce_gtid_consistency: str | None = None

    # Cloud SQL platform surface
    iam_authentication_enabled: bool = False
    iam_users: list[str] = field(default_factory=list)
    cloudsql_system_users: list[str] = field(default_factory=list)
    has_heartbeat_table: bool = False  # mysql.heartbeat, replicates if not filtered

    # Replication topology. Requires REPLICATION CLIENT; absent grants degrade
    # to "unknown" rather than failing the scan.
    read_only: bool | None = None
    super_read_only: bool | None = None
    is_replica: bool = False  # this instance replicates from another
    replica_source_host: str | None = None
    connected_replica_count: int = 0
    connected_replica_hosts: list[str] = field(default_factory=list)


@dataclass
class QueryLogSignals:
    """Facts that would require query-log analysis to detect.

    No query-log collector exists, so these default to "none detected" and
    reports must render them as *not detected*, never as *clear*. Callers can
    override them once such a collector exists, or when an operator has
    confirmed the answer by other means.

    xa_detected -> BLOCKER-5, udf_count -> BLOCKER-6, xml_function_detected ->
    BLOCKER-7, get_lock_detected -> WARNING-5, sql_calc_found_rows_detected ->
    WARNING-6, savepoint_detected -> WARNING-7.
    """

    xa_detected: bool = False
    udf_count: int = 0
    xml_function_detected: bool = False
    get_lock_detected: bool = False
    sql_calc_found_rows_detected: bool = False
    savepoint_detected: bool = False


@dataclass
class BinlogVariableCheck:
    """One row of the binlog/continue-replication readiness precheck."""

    variable: str
    rule_id: str | None  # None for informational-only checks
    actual: str | None
    required: str
    status: str  # pass | fail | warn | info
    why: str
    recommended: str | None = None
    # How to fix it on Cloud SQL specifically. Rendered verbatim into reports,
    # because `SET GLOBAL` fails with ERROR 1227 on every one of these and the
    # correct remediation differs per variable (instance flag vs PITR setting).
    remediation: str = ""


@dataclass
class BinlogPrecheckResult:
    checks: list[BinlogVariableCheck] = field(default_factory=list)
    continue_replication_ready: bool = True  # False if any gated check failed


@dataclass
class ColumnInfo:
    schema_name: str
    table_name: str
    column_name: str
    ordinal_position: int
    data_type: str
    column_type: str  # full type incl. length/unsigned, e.g. "bigint unsigned"
    is_nullable: bool
    column_default: str | None = None
    character_maximum_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None
    charset: str | None = None
    collation: str | None = None
    extra: str = ""  # auto_increment, VIRTUAL GENERATED, ...


@dataclass
class TableInfo:
    schema_name: str
    table_name: str
    engine: str
    row_estimate: int
    data_bytes: int
    index_bytes: int
    create_options: str = ""  # raw CREATE_OPTIONS, e.g. row_format=COMPRESSED
    charset: str | None = None
    collation: str | None = None
    auto_increment: int | None = None
    partition_method: str | None = None
    columns: list[ColumnInfo] = field(default_factory=list)


@dataclass
class IndexInfo:
    schema_name: str
    table_name: str
    index_name: str
    index_type: str  # BTREE, FULLTEXT, SPATIAL
    is_unique: bool
    columns: list[str] = field(default_factory=list)


@dataclass
class ConstraintInfo:
    schema_name: str
    table_name: str
    constraint_name: str
    constraint_type: str  # PRIMARY KEY, FOREIGN KEY, UNIQUE, CHECK
    definition: str = ""
    foreign_table: str | None = None


@dataclass
class RoutineInfo:
    schema_name: str
    routine_name: str
    kind: str  # PROCEDURE or FUNCTION
    definition: str
    definer: str = ""  # user@host; CSQL-WARNING-2 when it names a Cloud SQL principal
    is_deterministic: bool = False


@dataclass
class TriggerInfo:
    schema_name: str
    table_name: str
    trigger_name: str
    timing: str  # BEFORE / AFTER
    event: str  # INSERT / UPDATE / DELETE
    definition: str = ""
    definer: str = ""


@dataclass
class EventInfo:
    schema_name: str
    event_name: str
    schedule: str
    definition: str = ""
    definer: str = ""


@dataclass
class ViewInfo:
    schema_name: str
    view_name: str
    is_updatable: bool = False  # information_schema.VIEWS.IS_UPDATABLE == 'YES'
    definer: str = ""


@dataclass
class SchemaInventory:
    tables: list[TableInfo] = field(default_factory=list)
    columns: list[ColumnInfo] = field(default_factory=list)
    indexes: list[IndexInfo] = field(default_factory=list)
    constraints: list[ConstraintInfo] = field(default_factory=list)
    routines: list[RoutineInfo] = field(default_factory=list)
    triggers: list[TriggerInfo] = field(default_factory=list)
    events: list[EventInfo] = field(default_factory=list)
    views: list[ViewInfo] = field(default_factory=list)
    # Derived roll-ups, computed once by the collector so every rule that needs
    # them reads the same list instead of re-deriving it slightly differently.
    non_innodb_tables: list[str] = field(default_factory=list)
    unsupported_collations: list[str] = field(default_factory=list)
    # Objects whose DEFINER names a principal that cannot exist on TiDB.
    definer_objects: list[str] = field(default_factory=list)


@dataclass
class CleanupFinding:
    """One DDL cleanup rule hit (see rules/ddl_cleanup.py)."""

    rule_id: str
    risk: str  # blocker | assess | info | harmless
    table: str | None
    matched_text: str
    action_taken: str  # commented_out | rewritten | commented_out_with_suggestion | kept
    suggestion: str | None = None


@dataclass
class DDLCleanupResult:
    """Output of the convert-phase DDL cleanup over a whole script."""

    sql: str = ""
    findings: list[CleanupFinding] = field(default_factory=list)
    # Tables with a FULLTEXT index (CSQL-DDL-5) — the index is commented out and
    # a TiFlash replica stands in for it via scan-based filtering.
    fulltext_tables: list[str] = field(default_factory=list)
    # Tables carrying spatial indexes or columns (CSQL-DDL-6) — commented out
    # and flagged for review; there is no mechanical equivalent.
    spatial_tables: list[str] = field(default_factory=list)
    tiflash_statements: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


@dataclass
class CompatibilityFinding:
    rule_id: str
    severity: Severity
    feature: str
    count: int
    action: str


@dataclass
class AssessmentResult:
    blockers: list[CompatibilityFinding] = field(default_factory=list)
    warnings: list[CompatibilityFinding] = field(default_factory=list)
    compatible: list[str] = field(default_factory=list)


@dataclass
class CategoryScore:
    name: str
    max_points: int
    score: int
    deductions: list[str] = field(default_factory=list)


@dataclass
class ReadinessScore:
    overall: int
    categories: list[CategoryScore] = field(default_factory=list)
    rating: str = ""
