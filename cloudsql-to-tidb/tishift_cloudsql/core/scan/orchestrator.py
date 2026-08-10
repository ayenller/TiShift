"""Scan orchestration — Phase 2 + 3 (Scan, Assess & Score).

Wires the already-tested collectors and analyzers together over one open
connection. Kept thin on purpose: almost all the logic lives in those pure
functions, so this module stays easy to unit-test with a scripted fake
connection despite touching "the whole scan".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymysql

from tishift_cloudsql.core.scan.analyzers.binlog_check import evaluate_binlog_config
from tishift_cloudsql.core.scan.analyzers.compatibility import assess_compatibility
from tishift_cloudsql.core.scan.analyzers.scoring import ScoringContext, compute_readiness_score
from tishift_cloudsql.core.scan.collectors.binlog import fetch_binlog_variables
from tishift_cloudsql.core.scan.collectors.platform import collect_platform_metadata
from tishift_cloudsql.core.scan.collectors.schema import collect_schema_inventory
from tishift_cloudsql.core.scan.collectors.valid_indexes import fetch_tables_without_valid_index
from tishift_cloudsql.models import (
    AssessmentResult,
    BinlogPrecheckResult,
    CloudSQLMetadata,
    QueryLogSignals,
    ReadinessScore,
    SchemaInventory,
)
from tishift_cloudsql.rules.compatibility import CompatibilityContext


@dataclass
class ScanResult:
    schema: str
    tier: str
    continue_replication_planned: bool
    metadata: CloudSQLMetadata
    inventory: SchemaInventory
    binlog: BinlogPrecheckResult
    tables_without_valid_index: list[str] = field(default_factory=list)
    assessment: AssessmentResult = field(default_factory=AssessmentResult)
    score: ReadinessScore = field(default_factory=lambda: ReadinessScore(overall=0))
    total_size_bytes: int = 0


def run_scan(
    conn: pymysql.Connection,
    schema: str,
    tier: str = "starter",
    continue_replication_planned: bool = False,
    network_path_confirmed: bool = True,
    export_bucket_configured: bool = True,
    query_log: QueryLogSignals | None = None,
) -> ScanResult:
    """Run every scan collector against *conn* and produce the full
    compatibility assessment and readiness score.

    The valid-indexes precheck only runs when *continue_replication_planned*
    is set — it is specific to DM, and it is a full-instance scan not worth
    paying for on a cutover-only migration.
    """
    query_log = query_log or QueryLogSignals()

    metadata = collect_platform_metadata(conn)
    inventory = collect_schema_inventory(conn, schema)
    binlog = evaluate_binlog_config(fetch_binlog_variables(conn))
    tables_without_index = (
        fetch_tables_without_valid_index(conn) if continue_replication_planned else []
    )

    total_size_bytes = sum(t.data_bytes + t.index_bytes for t in inventory.tables)

    compat_ctx = CompatibilityContext(
        inventory=inventory,
        metadata=metadata,
        binlog=binlog,
        tier=tier,
        continue_replication_planned=continue_replication_planned,
        query_log=query_log,
    )

    assessment = assess_compatibility(
        inventory,
        metadata,
        binlog,
        tier=tier,
        continue_replication_planned=continue_replication_planned,
        query_log=query_log,
    )

    score = compute_readiness_score(
        ScoringContext(
            compat=compat_ctx,
            total_size_bytes=total_size_bytes,
            tables_without_valid_index=len(tables_without_index),
            network_path_confirmed=network_path_confirmed,
            export_bucket_configured=export_bucket_configured,
        )
    )

    return ScanResult(
        schema=schema,
        tier=tier,
        continue_replication_planned=continue_replication_planned,
        metadata=metadata,
        inventory=inventory,
        binlog=binlog,
        tables_without_valid_index=tables_without_index,
        assessment=assessment,
        score=score,
        total_size_bytes=total_size_bytes,
    )
