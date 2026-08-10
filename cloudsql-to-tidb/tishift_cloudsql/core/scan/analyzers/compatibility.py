"""Compatibility analyzer — Phase 3 (Assess & Score).

Pure function over collected scan data (SchemaInventory, CloudSQLMetadata,
BinlogPrecheckResult) — no DB access — so it is testable against fixtures.
Applies every rule in rules/compatibility.py and returns an AssessmentResult
matching references/compatibility-rules.md's output format.
"""

from __future__ import annotations

from tishift_cloudsql.models import (
    AssessmentResult,
    BinlogPrecheckResult,
    CloudSQLMetadata,
    CompatibilityFinding,
    QueryLogSignals,
    SchemaInventory,
    Severity,
)
from tishift_cloudsql.rules.compatibility import (
    BLOCKER_RULES,
    COMPATIBLE_FEATURES,
    WARNING_RULES,
    CompatibilityContext,
)


def assess_compatibility(
    inventory: SchemaInventory,
    metadata: CloudSQLMetadata,
    binlog: BinlogPrecheckResult,
    tier: str = "starter",
    continue_replication_planned: bool = False,
    query_log: QueryLogSignals | None = None,
) -> AssessmentResult:
    """Evaluate every compatibility rule and return the assessment result."""
    ctx = CompatibilityContext(
        inventory=inventory,
        metadata=metadata,
        binlog=binlog,
        tier=tier,
        continue_replication_planned=continue_replication_planned,
        query_log=query_log or QueryLogSignals(),
    )

    result = AssessmentResult()

    for rule in BLOCKER_RULES:
        count = rule.check(ctx)
        if count > 0:
            result.blockers.append(
                CompatibilityFinding(
                    rule_id=rule.rule_id,
                    severity=Severity.BLOCKER,
                    feature=rule.feature,
                    count=count,
                    action=rule.action,
                )
            )

    for rule in WARNING_RULES:
        count = rule.check(ctx)
        if count > 0:
            result.warnings.append(
                CompatibilityFinding(
                    rule_id=rule.rule_id,
                    severity=Severity.WARNING,
                    feature=rule.feature,
                    count=count,
                    action=rule.action,
                )
            )

    result.compatible = list(COMPATIBLE_FEATURES)

    return result
