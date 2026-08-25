from __future__ import annotations

from tishift_cloudsql.core.scan.analyzers.scoring import ScoringContext, compute_readiness_score
from tishift_cloudsql.models import (
    BinlogPrecheckResult,
    BinlogVariableCheck,
    CloudSQLMetadata,
    ColumnInfo,
    EventInfo,
    RoutineInfo,
    SchemaInventory,
    TableInfo,
    TriggerInfo,
)
from tishift_cloudsql.rules.compatibility import CompatibilityContext
from tishift_cloudsql.rules.scoring import CATEGORY_MAX_POINTS, rating_for_score

STRICT_SQL_MODE = (
    "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
    "ERROR_FOR_DIVISION_BY_ZERO"
)


def _ctx(inventory=None, metadata=None, binlog=None, tier="essential", **kwargs):
    compat = CompatibilityContext(
        inventory=inventory or SchemaInventory(),
        metadata=metadata
        or CloudSQLMetadata(
            mysql_version="8.0.37-google", lower_case_table_names=2, sql_mode=STRICT_SQL_MODE
        ),
        binlog=binlog or BinlogPrecheckResult(),
        tier=tier,
        continue_replication_planned=kwargs.pop("continue_replication_planned", False),
    )
    return ScoringContext(compat=compat, **kwargs)


def _category(score, name):
    return next(c for c in score.categories if c.name == name)


def test_pristine_source_scores_100() -> None:
    score = compute_readiness_score(_ctx())
    assert score.overall == 100
    assert score.rating == "READY"


def test_category_maxima_sum_to_100() -> None:
    assert sum(CATEGORY_MAX_POINTS.values()) == 100


def test_rating_bands() -> None:
    assert rating_for_score(100) == "READY"
    assert rating_for_score(85) == "READY"
    assert rating_for_score(84) == "READY WITH WORK"
    assert rating_for_score(65) == "READY WITH WORK"
    assert rating_for_score(64) == "SIGNIFICANT REWORK"
    assert rating_for_score(40) == "SIGNIFICANT REWORK"
    assert rating_for_score(39) == "NOT RECOMMENDED YET"
    assert rating_for_score(0) == "NOT RECOMMENDED YET"


def _column(table, dtype="int", charset=None) -> ColumnInfo:
    return ColumnInfo(
        schema_name="s",
        table_name=table,
        column_name="c",
        ordinal_position=1,
        data_type=dtype,
        column_type=dtype,
        is_nullable=True,
        charset=charset,
    )


def test_spatial_columns_deduct_per_table() -> None:
    inv = SchemaInventory(columns=[_column("a", "point"), _column("b", "polygon")])
    cat = _category(compute_readiness_score(_ctx(inv)), "Schema compatibility")
    assert cat.score == CATEGORY_MAX_POINTS["Schema compatibility"] - 10
    assert any("BLOCKER-4" in d for d in cat.deductions)


def test_categories_floor_at_zero() -> None:
    routines = [
        RoutineInfo(schema_name="s", routine_name=f"p{i}", kind="PROCEDURE", definition="")
        for i in range(200)
    ]
    cat = _category(
        compute_readiness_score(_ctx(SchemaInventory(routines=routines))), "Programmable objects"
    )
    assert cat.score == 0


def test_stored_procedures_deduct_per_batch_of_ten() -> None:
    routines = [
        RoutineInfo(schema_name="s", routine_name=f"p{i}", kind="PROCEDURE", definition="")
        for i in range(11)
    ]
    cat = _category(
        compute_readiness_score(_ctx(SchemaInventory(routines=routines))), "Programmable objects"
    )
    # 11 procedures = 2 batches = -10
    assert cat.score == CATEGORY_MAX_POINTS["Programmable objects"] - 10


def test_events_deduct_per_event() -> None:
    events = [EventInfo(schema_name="s", event_name=f"e{i}", schedule="") for i in range(2)]
    cat = _category(
        compute_readiness_score(_ctx(SchemaInventory(events=events))), "Programmable objects"
    )
    assert cat.score == CATEGORY_MAX_POINTS["Programmable objects"] - 6


def test_triggers_and_procedures_both_deduct() -> None:
    inv = SchemaInventory(
        routines=[RoutineInfo(schema_name="s", routine_name="p", kind="PROCEDURE", definition="")],
        triggers=[
            TriggerInfo(schema_name="s", table_name="t", trigger_name="x", timing="BEFORE", event="INSERT")
        ],
    )
    cat = _category(compute_readiness_score(_ctx(inv)), "Programmable objects")
    assert cat.score == CATEGORY_MAX_POINTS["Programmable objects"] - 10


# --- Cloud SQL platform category -----------------------------------------


def test_iam_authentication_deducts() -> None:
    meta = CloudSQLMetadata(
        mysql_version="8.0.37", lower_case_table_names=2, sql_mode=STRICT_SQL_MODE,
        iam_authentication_enabled=True,
    )
    cat = _category(compute_readiness_score(_ctx(metadata=meta)), "Cloud SQL platform surface")
    assert cat.score == CATEGORY_MAX_POINTS["Cloud SQL platform surface"] - 4


def test_definer_deduction_is_capped() -> None:
    inv = SchemaInventory(definer_objects=[f"view s.v{i}" for i in range(50)])
    cat = _category(compute_readiness_score(_ctx(inv)), "Cloud SQL platform surface")
    assert cat.score == CATEGORY_MAX_POINTS["Cloud SQL platform surface"] - 5


def test_non_innodb_deduction_is_capped() -> None:
    inv = SchemaInventory(non_innodb_tables=[f"t{i}" for i in range(10)])
    cat = _category(compute_readiness_score(_ctx(inv)), "Cloud SQL platform surface")
    assert cat.score == CATEGORY_MAX_POINTS["Cloud SQL platform surface"] - 6


def test_mysql_57_deducts() -> None:
    meta = CloudSQLMetadata(
        mysql_version="5.7.44-google", lower_case_table_names=2, sql_mode=STRICT_SQL_MODE
    )
    cat = _category(compute_readiness_score(_ctx(metadata=meta)), "Cloud SQL platform surface")
    assert cat.score == CATEGORY_MAX_POINTS["Cloud SQL platform surface"] - 3


def test_lax_sql_mode_deducts_flat_not_per_mode() -> None:
    meta = CloudSQLMetadata(mysql_version="8.0.37", lower_case_table_names=2, sql_mode="")
    cat = _category(compute_readiness_score(_ctx(metadata=meta)), "Cloud SQL platform surface")
    assert cat.score == CATEGORY_MAX_POINTS["Cloud SQL platform surface"] - 2


# --- data & load ----------------------------------------------------------


def test_size_over_starter_cap_deducts() -> None:
    ctx = _ctx(tier="starter", total_size_bytes=30 * 1024**3)
    cat = _category(compute_readiness_score(ctx), "Data & load feasibility")
    assert cat.score == CATEGORY_MAX_POINTS["Data & load feasibility"] - 5


def test_size_under_cap_does_not_deduct() -> None:
    ctx = _ctx(tier="starter", total_size_bytes=1024**3)
    cat = _category(compute_readiness_score(ctx), "Data & load feasibility")
    assert cat.score == CATEGORY_MAX_POINTS["Data & load feasibility"]


def test_untiered_target_has_no_capacity_cap() -> None:
    ctx = _ctx(tier="dedicated", total_size_bytes=10 * 1024**4)
    cat = _category(compute_readiness_score(ctx), "Data & load feasibility")
    assert cat.score == CATEGORY_MAX_POINTS["Data & load feasibility"]


def test_missing_network_path_and_bucket_deduct() -> None:
    ctx = _ctx(network_path_confirmed=False, export_bucket_configured=False)
    cat = _category(compute_readiness_score(ctx), "Data & load feasibility")
    assert cat.score == CATEGORY_MAX_POINTS["Data & load feasibility"] - 7


# --- cutover & replication ------------------------------------------------


def _failing_binlog(rule_id: str) -> BinlogPrecheckResult:
    return BinlogPrecheckResult(
        checks=[
            BinlogVariableCheck(
                variable="v", rule_id=rule_id, actual="x", required="y", status="fail", why="z"
            )
        ],
        continue_replication_ready=False,
    )


def test_cutover_only_migration_is_not_penalised_for_binlog() -> None:
    ctx = _ctx(binlog=_failing_binlog("CSQL-WARNING-8"), continue_replication_planned=False)
    cat = _category(compute_readiness_score(ctx), "Cutover & continue replication")
    assert cat.score == CATEGORY_MAX_POINTS["Cutover & continue replication"]


def test_log_bin_off_deducts_when_replicating() -> None:
    ctx = _ctx(binlog=_failing_binlog("CSQL-WARNING-8"), continue_replication_planned=True)
    cat = _category(compute_readiness_score(ctx), "Cutover & continue replication")
    assert cat.score == CATEGORY_MAX_POINTS["Cutover & continue replication"] - 5


def test_read_replica_source_wipes_the_replication_category() -> None:
    meta = CloudSQLMetadata(
        mysql_version="8.0.37", lower_case_table_names=2, sql_mode=STRICT_SQL_MODE, is_replica=True
    )
    ctx = _ctx(metadata=meta, continue_replication_planned=True, tier="essential")
    cat = _category(compute_readiness_score(ctx), "Cutover & continue replication")
    assert cat.score == 0
    assert any("CSQL-BLOCKER-1" in d for d in cat.deductions)


def test_starter_tier_cannot_continue_replicate() -> None:
    ctx = _ctx(tier="starter", continue_replication_planned=True)
    cat = _category(compute_readiness_score(ctx), "Cutover & continue replication")
    assert any("Starter" in d for d in cat.deductions)


def test_tables_without_valid_index_deduct_per_table() -> None:
    ctx = _ctx(continue_replication_planned=True, tables_without_valid_index=3)
    cat = _category(compute_readiness_score(ctx), "Cutover & continue replication")
    assert cat.score == CATEGORY_MAX_POINTS["Cutover & continue replication"] - 6


def test_valid_index_deduction_skipped_for_cutover_only() -> None:
    ctx = _ctx(continue_replication_planned=False, tables_without_valid_index=3)
    cat = _category(compute_readiness_score(ctx), "Cutover & continue replication")
    assert cat.score == CATEGORY_MAX_POINTS["Cutover & continue replication"]


def test_zero_penalty_deductions_are_still_reported() -> None:
    # utf8mb4_0900_* costs nothing but is worth telling the reader about.
    col = ColumnInfo(
        schema_name="s", table_name="t", column_name="c", ordinal_position=1,
        data_type="varchar", column_type="varchar(10)", is_nullable=True,
        collation="utf8mb4_0900_ai_ci",
    )
    score = compute_readiness_score(_ctx(SchemaInventory(columns=[col])))
    cat = _category(score, "Schema compatibility")
    assert cat.score == CATEGORY_MAX_POINTS["Schema compatibility"]
    assert any("-0:" in d and "WARNING-4" in d for d in cat.deductions)


def test_overall_is_the_sum_of_categories() -> None:
    inv = SchemaInventory(
        tables=[TableInfo(schema_name="s", table_name="t", engine="InnoDB", row_estimate=0, data_bytes=0, index_bytes=0)],
        events=[EventInfo(schema_name="s", event_name="e", schedule="")],
        non_innodb_tables=["x"],
    )
    score = compute_readiness_score(_ctx(inv))
    assert score.overall == sum(c.score for c in score.categories)


def test_fk_without_unique_parent_index_is_reported_with_no_penalty() -> None:
    # Verified on TiDB v8.5.3: these foreign keys are accepted AND enforced, so
    # they cost nothing against *this* target. They are still surfaced, because
    # MySQL 8.0.16+ rejects them and that breaks rollback/staging portability.
    inv = SchemaInventory(fks_without_unique_parent_index=["child.fk1 -> parent(a)"])
    score = compute_readiness_score(_ctx(inv))
    cat = _category(score, "Cloud SQL platform surface")
    assert cat.score == CATEGORY_MAX_POINTS["Cloud SQL platform surface"]
    assert any("-0:" in d and "CSQL-WARNING-14" in d for d in cat.deductions)
