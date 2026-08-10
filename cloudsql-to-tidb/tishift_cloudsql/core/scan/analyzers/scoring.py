"""Readiness scoring engine — Phase 3 (Assess & Score).

Pure function over the same CompatibilityContext the compatibility analyzer
uses, plus a few scoring-only facts (total size, valid-index count, network
path, export bucket) bundled in ScoringContext.

Reuses the exact same `rule.check()` functions from rules/compatibility.py, so
the findings table and the score deductions can never disagree about what they
counted. Constants live in rules/scoring.py; the formulas live here because
they are heterogeneous enough (batching, caps, tier gating) that a generic
table would obscure more than it shared.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from tishift_cloudsql.models import CategoryScore, ReadinessScore
from tishift_cloudsql.rules.compatibility import ALL_RULES, CompatibilityContext
from tishift_cloudsql.rules.scoring import (
    BATCH_SIZE,
    CATEGORY_MAX_POINTS,
    POINTS,
    TIER_CAPACITY_BYTES,
    rating_for_score,
)


@dataclass
class ScoringContext:
    compat: CompatibilityContext
    total_size_bytes: int | None = None  # None = not measured / unknown
    tables_without_valid_index: int = 0
    network_path_confirmed: bool = True
    export_bucket_configured: bool = True


def _rule_counts(compat_ctx: CompatibilityContext) -> dict[str, int]:
    return {rule.rule_id: rule.check(compat_ctx) for rule in ALL_RULES}


def _clamp(score: int, max_points: int) -> int:
    return max(0, min(score, max_points))


def _schema_compatibility(ctx: ScoringContext, counts: dict[str, int]) -> CategoryScore:
    max_points = CATEGORY_MAX_POINTS["Schema compatibility"]
    deductions: list[str] = []
    score = max_points

    spatial = counts["BLOCKER-4"]
    if spatial:
        points = spatial * POINTS["spatial_column_set"]
        score -= points
        deductions.append(f"-{points}: {spatial} table(s) with spatial columns (BLOCKER-4)")

    charset = counts["BLOCKER-8"]
    if charset:
        points = charset * POINTS["unsupported_charset_table"]
        score -= points
        deductions.append(
            f"-{points}: {charset} table(s) with an unsupported character set — only "
            f"ascii/latin1/binary/utf8/utf8mb4/gbk are supported (BLOCKER-8)"
        )

    case_collisions = counts["BLOCKER-9"]
    if case_collisions:
        points = case_collisions * POINTS["table_name_case_collision"]
        score -= points
        deductions.append(
            f"-{points}: {case_collisions} table name(s) collide once case is folded — "
            f"not fixable on the Cloud SQL side, requires a rename (BLOCKER-9)"
        )

    collation = counts["WARNING-4"]
    if collation:
        deductions.append(
            f"-0: {collation} table(s) with utf8mb4_0900_* collation — maps 1:1, "
            f"supported natively since TiDB v7.4, no penalty (WARNING-4)"
        )

    fulltext = counts["WARNING-2"]
    if fulltext:
        points = fulltext * POINTS["fulltext_index_no_real_index"]
        score -= points
        deductions.append(
            f"-{points}: {fulltext} FULLTEXT index(es) on {ctx.compat.tier} target — real "
            f"index support is Starter-only (WARNING-2)"
        )

    lc_mismatch = counts["WARNING-8"]
    if lc_mismatch:
        points = POINTS["lower_case_table_names_mismatch"]
        score -= points
        deductions.append(
            f"-{points}: lower_case_table_names={ctx.compat.metadata.lower_case_table_names} "
            f"on source; TiDB Cloud only supports 2 (WARNING-8)"
        )

    updatable_views = counts["WARNING-9"]
    if updatable_views:
        points = updatable_views * POINTS["updatable_view"]
        score -= points
        deductions.append(
            f"-{points}: {updatable_views} updatable view(s) — TiDB views are always "
            f"read-only (WARNING-9)"
        )

    return CategoryScore(
        name="Schema compatibility",
        max_points=max_points,
        score=_clamp(score, max_points),
        deductions=deductions,
    )


def _programmable_objects(ctx: ScoringContext, counts: dict[str, int]) -> CategoryScore:
    max_points = CATEGORY_MAX_POINTS["Programmable objects"]
    deductions: list[str] = []
    score = max_points

    procs = counts["BLOCKER-1"]
    if procs:
        batches = math.ceil(procs / BATCH_SIZE)
        points = batches * POINTS["stored_procedure_batch"]
        score -= points
        deductions.append(
            f"-{points}: {procs} stored procedure(s) ({batches} batch(es) of {BATCH_SIZE}, BLOCKER-1)"
        )

    triggers = counts["BLOCKER-2"]
    if triggers:
        batches = math.ceil(triggers / BATCH_SIZE)
        points = batches * POINTS["trigger_batch"]
        score -= points
        deductions.append(
            f"-{points}: {triggers} trigger(s) ({batches} batch(es) of {BATCH_SIZE}, BLOCKER-2)"
        )

    events = counts["BLOCKER-3"]
    if events:
        points = events * POINTS["event"]
        score -= points
        deductions.append(f"-{points}: {events} scheduled event(s) (BLOCKER-3)")

    if counts["BLOCKER-6"] > 0:
        points = POINTS["udfs_present"]
        score -= points
        deductions.append(f"-{points}: user-defined functions present (BLOCKER-6)")

    if counts["BLOCKER-5"] > 0:
        points = POINTS["xa_present"]
        score -= points
        deductions.append(f"-{points}: XA transactions detected (BLOCKER-5)")

    return CategoryScore(
        name="Programmable objects",
        max_points=max_points,
        score=_clamp(score, max_points),
        deductions=deductions,
    )


def _cloudsql_platform_surface(ctx: ScoringContext, counts: dict[str, int]) -> CategoryScore:
    """The managed-platform category — what makes a Cloud SQL migration
    different from a self-managed MySQL one."""
    max_points = CATEGORY_MAX_POINTS["Cloud SQL platform surface"]
    deductions: list[str] = []
    score = max_points

    if counts["CSQL-WARNING-1"] > 0:
        points = POINTS["iam_authentication"]
        score -= points
        deductions.append(
            f"-{points}: IAM database authentication in use — no TiDB equivalent, every "
            f"affected client needs new credentials (CSQL-WARNING-1)"
        )

    definers = counts["CSQL-WARNING-2"]
    if definers:
        points = min(definers * POINTS["definer_object"], POINTS["definer_object_max"])
        score -= points
        deductions.append(
            f"-{points}: {definers} object(s) with a DEFINER that cannot exist on TiDB "
            f"(CSQL-WARNING-2)"
        )

    if counts["CSQL-WARNING-3"] > 0:
        points = POINTS["mysql_57_source"]
        score -= points
        deductions.append(
            f"-{points}: MySQL 5.7 source — TiDB targets 8.0 semantics (CSQL-WARNING-3)"
        )

    lax_modes = counts["CSQL-WARNING-4"]
    if lax_modes:
        points = POINTS["sql_mode_mismatch"]
        score -= points
        deductions.append(
            f"-{points}: source sql_mode is missing {lax_modes} strictness mode(s) TiDB "
            f"enables by default — rows that insert today may be rejected (CSQL-WARNING-4)"
        )

    if counts["CSQL-WARNING-5"] > 0:
        points = POINTS["cloudsql_system_artifacts"]
        score -= points
        deductions.append(
            f"-{points}: Cloud SQL system artifacts in scope — exclude the mysql schema "
            f"from the dump and from DM (CSQL-WARNING-5)"
        )

    replicas = counts["CSQL-WARNING-6"]
    if replicas:
        points = POINTS["read_replicas_attached"]
        score -= points
        deductions.append(
            f"-{points}: {replicas} downstream read replica(s) to account for at cutover "
            f"(CSQL-WARNING-6)"
        )

    non_innodb = counts["CSQL-WARNING-7"]
    if non_innodb:
        points = min(non_innodb * POINTS["non_innodb_table"], POINTS["non_innodb_table_max"])
        score -= points
        deductions.append(
            f"-{points}: {non_innodb} non-InnoDB table(s) — TiDB has one engine (CSQL-WARNING-7)"
        )

    return CategoryScore(
        name="Cloud SQL platform surface",
        max_points=max_points,
        score=_clamp(score, max_points),
        deductions=deductions,
    )


def _data_and_load_feasibility(ctx: ScoringContext, counts: dict[str, int]) -> CategoryScore:
    max_points = CATEGORY_MAX_POINTS["Data & load feasibility"]
    deductions: list[str] = []
    score = max_points

    cap = TIER_CAPACITY_BYTES.get(ctx.compat.tier)
    if cap is not None and ctx.total_size_bytes is not None and ctx.total_size_bytes > cap:
        points = POINTS["size_exceeds_tier_capacity"]
        score -= points
        gib = ctx.total_size_bytes / 1024**3
        cap_gib = cap / 1024**3
        deductions.append(
            f"-{points}: total size {gib:.1f} GiB exceeds {ctx.compat.tier} capacity "
            f"({cap_gib:.0f} GiB)"
        )

    if not ctx.network_path_confirmed:
        points = POINTS["no_network_path"]
        score -= points
        deductions.append(
            f"-{points}: no confirmed network path to the source (Auth Proxy, authorized "
            f"public IP, or private IP)"
        )

    if not ctx.export_bucket_configured:
        points = POINTS["no_export_bucket"]
        score -= points
        deductions.append(
            f"-{points}: no GCS export bucket configured — the recommended "
            f"`gcloud sql export` path is unavailable, leaving only Dumpling over the proxy"
        )

    return CategoryScore(
        name="Data & load feasibility",
        max_points=max_points,
        score=_clamp(score, max_points),
        deductions=deductions,
    )


def _cutover_and_continue_replication(ctx: ScoringContext, counts: dict[str, int]) -> CategoryScore:
    max_points = CATEGORY_MAX_POINTS["Cutover & continue replication"]
    deductions: list[str] = []
    score = max_points
    planned = ctx.compat.continue_replication_planned

    if counts["CSQL-BLOCKER-1"] > 0:
        points = POINTS["source_is_read_replica"]
        score -= points
        deductions.append(
            f"-{points}: source is a Cloud SQL read replica — it cannot be the source of "
            f"an external replica at all (CSQL-BLOCKER-1)"
        )

    if planned and ctx.compat.tier == "starter":
        points = POINTS["continue_replication_required_but_starter"]
        score -= points
        deductions.append(
            f"-{points}: continue replication required but target tier is Starter (cutover-only)"
        )

    if counts["CSQL-WARNING-8"] > 0:
        points = POINTS["log_bin_off"]
        score -= points
        deductions.append(
            f"-{points}: binary logging is off — continue replication is categorically "
            f"impossible until it is enabled (CSQL-WARNING-8)"
        )

    if counts["CSQL-WARNING-9"] > 0 or counts["CSQL-WARNING-10"] > 0:
        points = POINTS["binlog_format_or_row_image"]
        score -= points
        reasons = []
        if counts["CSQL-WARNING-9"] > 0:
            reasons.append("binlog_format != ROW (CSQL-WARNING-9)")
        if counts["CSQL-WARNING-10"] > 0:
            reasons.append("binlog_row_image != FULL (CSQL-WARNING-10)")
        deductions.append(f"-{points}: {', '.join(reasons)}")

    if counts["CSQL-WARNING-11"] > 0:
        points = POINTS["binlog_retention"]
        score -= points
        deductions.append(
            f"-{points}: binlog retention below the minimum or the recommendation "
            f"(CSQL-WARNING-11)"
        )

    if counts["CSQL-WARNING-12"] > 0:
        points = POINTS["binlog_row_value_options"]
        score -= points
        deductions.append(
            f"-{points}: binlog_row_value_options is not empty — silent JSON corruption "
            f"risk under DM (CSQL-WARNING-12)"
        )

    if counts["CSQL-WARNING-13"] > 0:
        points = POINTS["binlog_transaction_compression"]
        score -= points
        deductions.append(
            f"-{points}: binlog_transaction_compression is not OFF (CSQL-WARNING-13)"
        )

    if planned and ctx.tables_without_valid_index > 0:
        points = ctx.tables_without_valid_index * POINTS["table_without_valid_index"]
        score -= points
        deductions.append(
            f"-{points}: {ctx.tables_without_valid_index} business table(s) without a "
            f"PK/UNIQUE index"
        )

    return CategoryScore(
        name="Cutover & continue replication",
        max_points=max_points,
        score=_clamp(score, max_points),
        deductions=deductions,
    )


def compute_readiness_score(ctx: ScoringContext) -> ReadinessScore:
    """Compute the 0-100 readiness score with per-category breakdowns."""
    counts = _rule_counts(ctx.compat)

    categories = [
        _schema_compatibility(ctx, counts),
        _programmable_objects(ctx, counts),
        _cloudsql_platform_surface(ctx, counts),
        _data_and_load_feasibility(ctx, counts),
        _cutover_and_continue_replication(ctx, counts),
    ]

    overall = sum(c.score for c in categories)
    return ReadinessScore(overall=overall, categories=categories, rating=rating_for_score(overall))
