"""Readiness scoring constants — scan Phase 3 (Assess & Score).

Single source of truth for category maxima, rating bands, and per-unit
deduction constants, kept in lockstep with references/scoring.md. The actual
category computation lives in core/scan/analyzers/scoring.py — the formulas
(batching, caps, tier gating) are heterogeneous enough that forcing them into
one generic table would hurt readability more than it would help reuse.

Rating bands are deliberately identical to every other TiShift module so a
Cloud SQL score and a HeatWave score mean the same thing.
"""

from __future__ import annotations

CATEGORY_MAX_POINTS: dict[str, int] = {
    "Schema compatibility": 30,
    "Programmable objects": 25,
    "Cloud SQL platform surface": 20,
    "Data & load feasibility": 15,
    "Cutover & continue replication": 10,
}

# (low, high, rating) — checked in order, first match wins.
RATING_BANDS: list[tuple[int, int, str]] = [
    (85, 100, "READY"),
    (65, 84, "READY WITH WORK"),
    (40, 64, "SIGNIFICANT REWORK"),
    (0, 39, "NOT RECOMMENDED YET"),
]

# Free-tier storage cap; tiers absent from this dict have no modeled hard cap.
TIER_CAPACITY_BYTES: dict[str, int] = {
    "starter": 25 * 1024**3,
}

BATCH_SIZE = 10  # stored procedures / triggers deducted per batch of this many

POINTS = {
    # Schema compatibility
    "spatial_column_set": 5,  # BLOCKER-4, per distinct table
    "unsupported_charset_table": 5,  # BLOCKER-8, per distinct table
    "table_name_case_collision": 5,  # BLOCKER-9, per colliding name group
    "fulltext_index_no_real_index": 2,  # WARNING-2, per index
    "lower_case_table_names_mismatch": 2,  # WARNING-8, flat
    "updatable_view": 1,  # WARNING-9, per view
    # Programmable objects
    "stored_procedure_batch": 5,  # BLOCKER-1, per batch of BATCH_SIZE
    "trigger_batch": 5,  # BLOCKER-2, per batch of BATCH_SIZE
    "event": 3,  # BLOCKER-3, per event
    "udfs_present": 5,  # BLOCKER-6, flat
    "xa_present": 3,  # BLOCKER-5, flat
    # Cloud SQL platform surface
    "iam_authentication": 4,  # CSQL-WARNING-1, flat
    "definer_object": 1,  # CSQL-WARNING-2, per object
    "definer_object_max": 5,  # cap on the above
    "mysql_57_source": 3,  # CSQL-WARNING-3, flat
    "sql_mode_mismatch": 2,  # CSQL-WARNING-4, flat
    "cloudsql_system_artifacts": 2,  # CSQL-WARNING-5, flat
    "read_replicas_attached": 2,  # CSQL-WARNING-6, flat
    "non_innodb_table": 3,  # CSQL-WARNING-7, per table
    "non_innodb_table_max": 6,  # cap on the above
    # Data & load feasibility
    "size_exceeds_tier_capacity": 5,  # flat
    "no_network_path": 5,  # flat
    "no_export_bucket": 2,  # flat
    # Cutover & continue replication
    "source_is_read_replica": 10,  # CSQL-BLOCKER-1, flat
    "continue_replication_required_but_starter": 5,  # flat
    "log_bin_off": 5,  # CSQL-WARNING-8, flat
    "binlog_format_or_row_image": 3,  # CSQL-WARNING-9/10, flat
    "binlog_retention": 2,  # CSQL-WARNING-11, flat
    "binlog_row_value_options": 2,  # CSQL-WARNING-12, flat
    "binlog_transaction_compression": 2,  # CSQL-WARNING-13, flat
    "table_without_valid_index": 2,  # per table, only when continue replication is planned
}


def rating_for_score(score: int) -> str:
    for low, high, label in RATING_BANDS:
        if low <= score <= high:
            return label
    return "NOT RECOMMENDED YET"
