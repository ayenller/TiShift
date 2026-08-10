"""Server and Cloud SQL platform metadata collector — SKILL.md Phase 1 and 2.1b.

Reads server identity, the settings not already covered by the binlog precheck
(collectors/binlog.py), and the Cloud SQL platform surface: IAM authentication,
system principals, and replication topology.

Every read is wrapped defensively so the collector degrades gracefully against
a plain MySQL server or a source whose user lacks `mysql.user` SELECT — a
missing grant produces "unknown", never a failed scan.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pymysql

from tishift_cloudsql.models import CloudSQLMetadata

_VAR_PATTERN = re.compile(r"@@[a-zA-Z_][a-zA-Z0-9_.]*")

# Cloud SQL identifies itself in @@version_comment. Kept as a substring check
# because the exact string has varied across versions.
_CLOUDSQL_VERSION_MARKER = "google"

# Cloud SQL's own bookkeeping principals — never migrate these.
_CLOUDSQL_USER_PREFIX = "cloudsql"

# IAM database users authenticate with an access token rather than a password.
# The plugin name is the reliable signal; the user name is not, because IAM
# users can be named anything.
_IAM_AUTH_PLUGIN_MARKER = "iam"


def _get_var(cursor: Any, expr: str) -> str | None:
    """Execute ``SELECT <expr>`` and return the scalar value, or None.

    Only @@system_variable expressions are allowed — this is the one place
    string interpolation into SQL is acceptable, and only because the pattern
    cannot carry injected SQL.
    """
    if not _VAR_PATTERN.fullmatch(expr):
        raise ValueError(f"Only @@system_variable expressions are allowed, got: {expr!r}")
    import pymysql

    try:
        cursor.execute(f"SELECT {expr}")
        row = cursor.fetchone()
        if row is None:
            return None
        return str(list(row.values())[0]) if row else None
    except pymysql.Error:
        return None


def _collect_users(cursor: Any, meta: CloudSQLMetadata) -> None:
    """Classify the instance's users into Cloud SQL system users and IAM users.

    Needs SELECT on `mysql.user`. A source account without it is common and not
    worth failing over — the fields stay empty and CSQL-WARNING-1/5 simply do
    not fire, which the report renders as "not detected".
    """
    import pymysql

    try:
        cursor.execute("SELECT user, host, plugin FROM mysql.user")
        rows = cursor.fetchall()
    except pymysql.Error:
        return

    for row in rows:
        user = str(row.get("user") or "")
        plugin = str(row.get("plugin") or "").lower()
        if user.lower().startswith(_CLOUDSQL_USER_PREFIX):
            meta.cloudsql_system_users.append(user)
        elif _IAM_AUTH_PLUGIN_MARKER in plugin:
            meta.iam_users.append(f"{user}@{row.get('host')}")

    if meta.iam_users:
        meta.iam_authentication_enabled = True


def _collect_heartbeat(cursor: Any, meta: CloudSQLMetadata) -> None:
    """Detect `mysql.heartbeat`, Cloud SQL's replication liveness table.

    It receives a write every second or so. Replicated into TiDB it produces an
    endless stream of no-op changes, which is why CSQL-WARNING-5 insists the
    `mysql` schema is excluded from DM's block-allow-list.
    """
    import pymysql

    try:
        cursor.execute(
            "SELECT COUNT(*) AS n FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = 'mysql' AND TABLE_NAME = 'heartbeat'"
        )
        row = cursor.fetchone()
        meta.has_heartbeat_table = bool(row and int(list(row.values())[0]) > 0)
    except pymysql.Error:
        pass


def _collect_replication_topology(cursor: Any, meta: CloudSQLMetadata) -> None:
    """Populate the replication topology fields on *meta*.

    Two things matter here for Cloud SQL specifically. If this instance is
    itself a replica, it cannot be the source of an external replica at all
    (CSQL-BLOCKER-1). If it has downstream replicas, each one is a stale read
    path that has to be dealt with explicitly at cutover (CSQL-WARNING-6).

    Requires REPLICATION CLIENT; degrades to "standalone" without it.
    """
    import pymysql

    read_only = _get_var(cursor, "@@read_only")
    meta.read_only = (read_only == "1") if read_only is not None else None

    super_read_only = _get_var(cursor, "@@super_read_only")
    meta.super_read_only = (super_read_only == "1") if super_read_only is not None else None

    try:
        cursor.execute("SHOW REPLICA STATUS")
        row = cursor.fetchone()
        if row:
            meta.is_replica = True
            meta.replica_source_host = row.get("Source_Host") or row.get("Master_Host")
    except pymysql.Error:
        pass

    try:
        cursor.execute("SHOW REPLICAS")
        rows = cursor.fetchall()
        meta.connected_replica_count = len(rows)
        meta.connected_replica_hosts = [r["Host"] for r in rows if r.get("Host")]
    except pymysql.Error:
        pass


def collect_platform_metadata(conn: pymysql.Connection) -> CloudSQLMetadata:
    """Collect server- and platform-level metadata used throughout scan/assess."""
    meta = CloudSQLMetadata()

    with conn.cursor() as cur:
        meta.mysql_version = _get_var(cur, "@@version")
        meta.version_comment = _get_var(cur, "@@version_comment")
        meta.is_cloudsql = _CLOUDSQL_VERSION_MARKER in (meta.version_comment or "").lower()

        meta.sql_mode = _get_var(cur, "@@sql_mode")
        meta.character_set_server = _get_var(cur, "@@character_set_server")
        meta.collation_server = _get_var(cur, "@@collation_server")
        meta.transaction_isolation = _get_var(cur, "@@transaction_isolation")
        meta.local_infile = _get_var(cur, "@@local_infile")
        meta.binlog_row_value_options = _get_var(cur, "@@binlog_row_value_options")
        meta.gtid_mode = _get_var(cur, "@@gtid_mode")
        meta.enforce_gtid_consistency = _get_var(cur, "@@enforce_gtid_consistency")

        lc_names = _get_var(cur, "@@lower_case_table_names")
        meta.lower_case_table_names = int(lc_names) if lc_names is not None else None

        max_conn = _get_var(cur, "@@max_connections")
        meta.max_connections = int(max_conn) if max_conn is not None else None

        # The IAM flag is only visible as a variable when it has been set, so
        # the user scan below is the primary signal and this is a fallback.
        iam_flag = _get_var(cur, "@@cloudsql_iam_authentication")
        if iam_flag is not None and iam_flag.strip().lower() in ("1", "on", "true"):
            meta.iam_authentication_enabled = True

        _collect_users(cur, meta)
        _collect_heartbeat(cur, meta)
        _collect_replication_topology(cur, meta)

    return meta
