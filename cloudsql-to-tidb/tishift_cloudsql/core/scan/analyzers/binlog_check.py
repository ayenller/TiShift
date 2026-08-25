"""Binlog / continue-replication readiness evaluator.

Pure function over a {variable: value} dict — no DB access — so it is testable
against fixtures without a live Cloud SQL connection. Rule definitions live in
rules/binlog_check.py; the human-facing version is in
references/compatibility-rules.md and docs/sync-guide.md.

This precheck only gates continue replication. A failing result does not block
a cutover-only migration.
"""

from __future__ import annotations

from tishift_cloudsql.models import BinlogPrecheckResult, BinlogVariableCheck
from tishift_cloudsql.rules.binlog_check import INFORMATIONAL_VARIABLES, REQUIRED_RULES


def evaluate_binlog_config(variables: dict[str, str | None]) -> BinlogPrecheckResult:
    """Validate collected SHOW VARIABLES output against the readiness rules."""
    result = BinlogPrecheckResult()

    for rule in REQUIRED_RULES:
        actual = variables.get(rule.variable)
        ok = rule.check(actual)
        status = "pass" if ok else "fail"
        # "warn" means the hard minimum is met but the recommendation is not —
        # a migration that will probably work, on a source that gives it no margin.
        if ok and rule.recommended_check is not None and not rule.recommended_check(actual):
            status = "warn"
        result.checks.append(
            BinlogVariableCheck(
                variable=rule.variable,
                rule_id=rule.rule_id,
                actual=actual,
                required=rule.required,
                status=status,
                why=rule.why,
                recommended=rule.recommended,
                # Carried into the report only when the check did not pass —
                # remediation text on a passing row is noise.
                remediation=rule.remediation if status != "pass" else "",
            )
        )
        if status == "fail":
            result.continue_replication_ready = False

    server_id = variables.get("server_id")
    result.checks.append(
        BinlogVariableCheck(
            variable="server_id",
            rule_id=None,
            actual=server_id,
            required="non-zero, unique per source",
            status="warn" if server_id in (None, "0") else "info",
            why=INFORMATIONAL_VARIABLES["server_id"],
        )
    )
    for variable in ("binlog_expire_logs_seconds", "gtid_mode", "enforce_gtid_consistency"):
        required = (
            "(verify out-of-band — see why)"
            if variable == "binlog_expire_logs_seconds"
            else "(informational only — Cloud SQL enforces GTID)"
        )
        result.checks.append(
            BinlogVariableCheck(
                variable=variable,
                rule_id=None,
                actual=variables.get(variable),
                required=required,
                status="info",
                why=INFORMATIONAL_VARIABLES[variable],
            )
        )

    return result
