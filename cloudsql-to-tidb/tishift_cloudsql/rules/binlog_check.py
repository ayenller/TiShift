"""Binlog / continue-replication readiness precheck — scan phase.

Single source of truth for the SHOW VARIABLES check that decides whether a
Cloud SQL for MySQL source is ready for TiDB DM continue replication (Phase 7 /
docs/sync-guide.md). Kept in lockstep with references/compatibility-rules.md
(§ Continue-replication (binlog) prechecks).

This precheck only gates continue replication — cutover-only migrations do not
need a passing binlog configuration and are not penalised for one.

What makes this different from the same check on a self-managed MySQL: not one
of these variables can be fixed with `SET GLOBAL`. Cloud SQL grants no user
SUPER, so every remediation is a `gcloud` command, an instance setting, or —
for two of them — nothing at all, because the variable is not exposed as a
configurable flag. Each rule therefore carries its own `remediation` string
rather than a generic "set X = Y", and the report renders it verbatim.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

QUERY = (
    "SHOW VARIABLES WHERE Variable_name IN "
    "('log_bin','server_id','binlog_format','binlog_row_image',"
    "'binlog_expire_logs_seconds','binlog_transaction_compression',"
    "'binlog_row_value_options','gtid_mode','enforce_gtid_consistency')"
)


@dataclass(frozen=True)
class BinlogRule:
    variable: str
    rule_id: str
    required: str  # human-readable requirement shown in reports
    why: str
    check: Callable[[str | None], bool]  # True if the actual value satisfies `required`
    remediation: str
    recommended: str | None = None
    recommended_check: Callable[[str | None], bool] | None = None


def _equals(expected: str) -> Callable[[str | None], bool]:
    def check(value: str | None) -> bool:
        return value is not None and value.strip().upper() == expected.upper()

    return check


def _empty() -> Callable[[str | None], bool]:
    def check(value: str | None) -> bool:
        return value is not None and value.strip() == ""

    return check


def _int_at_least(minimum: int) -> Callable[[str | None], bool]:
    def check(value: str | None) -> bool:
        try:
            return value is not None and int(value) >= minimum
        except ValueError:
            return False

    return check


# One rule per gated variable. Order matches the table in
# references/compatibility-rules.md so a report iterates in the order a reader
# sees it there.
REQUIRED_RULES: list[BinlogRule] = [
    BinlogRule(
        variable="log_bin",
        rule_id="CSQL-WARNING-8",
        required="ON",
        why="Enables binary logging, which DM replays to replicate changes into TiDB",
        check=_equals("ON"),
        remediation=(
            "Not a database flag, and the Console does not call it 'binary logging' — "
            "it is Point-in-time recovery. Console: Edit -> Data Protection -> "
            "check 'Enable point-in-time recovery' -> set retention days -> Save. "
            "gcloud: `gcloud sql instances patch <INSTANCE> --enable-bin-log "
            "--retained-transaction-log-days=N` (use --enable-bin-log, NOT "
            "--enable-point-in-time-recovery, which is the PostgreSQL flag). "
            "Requires automatic backups on; verify with "
            "`gcloud sql instances describe <INSTANCE>` -> backupConfiguration.enabled. "
            "Enabling it RESTARTS the instance — schedule a window."
        ),
    ),
    BinlogRule(
        variable="binlog_format",
        rule_id="CSQL-WARNING-9",
        required="ROW",
        why="Captures all data changes accurately; other formats miss edge cases",
        check=_equals("ROW"),
        remediation=(
            "Not user-configurable — Cloud SQL sets binlog_format=ROW itself once binary "
            "logging is on. A non-ROW value means binary logging is off, or this is not "
            "the managed instance it appears to be. Investigate before patching anything."
        ),
    ),
    BinlogRule(
        variable="binlog_row_image",
        rule_id="CSQL-WARNING-10",
        required="FULL",
        why="Includes every column value in each event, which DM needs for safe conflict resolution",
        check=_equals("FULL"),
        remediation=(
            "`gcloud sql instances patch <INSTANCE> --database-flags=binlog_row_image=full` "
            "(configurable, no restart). Remember --database-flags REPLACES the whole flag "
            "list — restate the instance's existing flags in the same command."
        ),
    ),
    BinlogRule(
        variable="binlog_transaction_compression",
        rule_id="CSQL-WARNING-13",
        required="OFF",
        why="DM cannot read compressed transaction payloads",
        check=_equals("OFF"),
        remediation=(
            "Not an exposed Cloud SQL flag, and off by default. If this reads ON, "
            "open a support case — there is no patch that turns it off."
        ),
    ),
    BinlogRule(
        variable="binlog_row_value_options",
        rule_id="CSQL-WARNING-12",
        required="'' (empty, not PARTIAL_JSON)",
        why=(
            "DM cannot parse binlog rows written under partial-JSON mode — PARTIAL_JSON "
            "causes silent corruption of JSON columns, not a clean failure"
        ),
        check=_empty(),
        remediation=(
            "There is no 'unset one flag' verb. --database-flags replaces the entire list, "
            "so re-issue it without binlog_row_value_options, or use "
            "`gcloud sql instances patch <INSTANCE> --clear-database-flags` if it is the "
            "only flag set."
        ),
    ),
]

# Collected by the same query for visibility, but not gated: these are context
# a reader needs, not requirements DM imposes.
INFORMATIONAL_VARIABLES: dict[str, str] = {
    "binlog_expire_logs_seconds": (
        "NOT the authoritative retention on Cloud SQL, and not gated here. Cloud SQL "
        "retains transaction logs for PITR according to the instance-level "
        "transactionLogRetentionDays, which is a separate setting the MySQL protocol "
        "does not expose. Observed on a real instance: transactionLogRetentionDays was "
        "raised 1 -> 7 while this variable stayed at 86400, and it was not even present "
        "in the instance's databaseFlags. Gating on this value would WARN forever on a "
        "correctly configured instance. Check the real one with: "
        "`gcloud sql instances describe <INSTANCE> "
        "--format='value(settings.backupConfiguration.transactionLogRetentionDays)'`"
    ),
    "server_id": (
        "Must be non-zero and unique per source; 0 disables binary logging entirely, "
        "which breaks replication silently rather than failing cleanly. Cloud SQL "
        "regenerates this value across a full instance stop/start — observed changing "
        "from 1838753873 to 3608002274 — but NOT across a config-change restart, where "
        "it survived intact. Either way, do not build anything that assumes it is "
        "stable. GTID keys on server_uuid, which was observed unchanged across a "
        "PITR-enable restart, so GTID-based resume is unaffected"
    ),
    "gtid_mode": (
        "Cloud SQL enforces GTID and it cannot be turned off. This is good for DM — "
        "GTID-based resume is position-independent — and it is also why the "
        "enforce_gtid_consistency statement restrictions already apply to your source, "
        "so nothing new breaks at migration time"
    ),
    "enforce_gtid_consistency": (
        "Enforced ON alongside gtid_mode. Statements it already rejects on the source "
        "(CREATE TABLE ... SELECT, temporary tables inside a transaction) are therefore "
        "not a new migration risk"
    ),
}
