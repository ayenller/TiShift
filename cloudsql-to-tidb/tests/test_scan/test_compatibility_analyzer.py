from __future__ import annotations

import pytest

from tishift_cloudsql.core.scan.analyzers.compatibility import assess_compatibility
from tishift_cloudsql.models import (
    BinlogPrecheckResult,
    BinlogVariableCheck,
    CloudSQLMetadata,
    ColumnInfo,
    EventInfo,
    IndexInfo,
    QueryLogSignals,
    RoutineInfo,
    SchemaInventory,
    TableInfo,
    TriggerInfo,
    ViewInfo,
)

STRICT_SQL_MODE = (
    "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
    "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
)


def _meta(**kwargs) -> CloudSQLMetadata:
    defaults = {
        "mysql_version": "8.0.37-google",
        "lower_case_table_names": 0,
        "sql_mode": STRICT_SQL_MODE,
    }
    return CloudSQLMetadata(**{**defaults, **kwargs})


def _column(table="t", name="c", **kwargs) -> ColumnInfo:
    defaults = {
        "schema_name": "s",
        "table_name": table,
        "column_name": name,
        "ordinal_position": 1,
        "data_type": "int",
        "column_type": "int",
        "is_nullable": True,
    }
    return ColumnInfo(**{**defaults, **kwargs})


def _table(name="t", engine="InnoDB", **kwargs) -> TableInfo:
    defaults = {
        "schema_name": "s",
        "table_name": name,
        "engine": engine,
        "row_estimate": 0,
        "data_bytes": 0,
        "index_bytes": 0,
    }
    return TableInfo(**{**defaults, **kwargs})


def _assess(inventory=None, metadata=None, binlog=None, **kwargs):
    return assess_compatibility(
        inventory or SchemaInventory(),
        metadata or _meta(),
        binlog or BinlogPrecheckResult(),
        **kwargs,
    )


def _ids(findings) -> set[str]:
    return {f.rule_id for f in findings}


def _finding(result, rule_id):
    for f in [*result.blockers, *result.warnings]:
        if f.rule_id == rule_id:
            return f
    raise AssertionError(f"{rule_id} not among {_ids([*result.blockers, *result.warnings])}")


# --- generic MySQL rules --------------------------------------------------


def test_clean_schema_has_no_blockers() -> None:
    result = _assess(SchemaInventory(tables=[_table()]), _meta(lower_case_table_names=2))
    assert result.blockers == []


def test_stored_procedures_block() -> None:
    inv = SchemaInventory(
        routines=[RoutineInfo(schema_name="s", routine_name="p", kind="PROCEDURE", definition="")]
    )
    assert _finding(_assess(inv), "BLOCKER-1").count == 1


def test_functions_are_not_counted_as_procedures() -> None:
    inv = SchemaInventory(
        routines=[RoutineInfo(schema_name="s", routine_name="f", kind="FUNCTION", definition="")]
    )
    assert "BLOCKER-1" not in _ids(_assess(inv).blockers)


def test_triggers_and_events_block() -> None:
    inv = SchemaInventory(
        triggers=[TriggerInfo(schema_name="s", table_name="t", trigger_name="x", timing="BEFORE", event="INSERT")],
        events=[EventInfo(schema_name="s", event_name="e", schedule="EVERY 1 DAY")],
    )
    ids = _ids(_assess(inv).blockers)
    assert {"BLOCKER-2", "BLOCKER-3"} <= ids


def test_spatial_columns_block_once_per_table() -> None:
    inv = SchemaInventory(
        columns=[
            _column(table="geo", name="a", data_type="point"),
            _column(table="geo", name="b", data_type="polygon"),
            _column(table="geo2", name="c", data_type="geometry"),
        ]
    )
    assert _finding(_assess(inv), "BLOCKER-4").count == 2


def test_unsupported_charset_blocks() -> None:
    inv = SchemaInventory(columns=[_column(charset="ucs2")])
    assert _finding(_assess(inv), "BLOCKER-8").count == 1


@pytest.mark.parametrize("charset", ["utf8", "utf8mb3", "utf8mb4", "latin1", "ascii", "binary", "gbk"])
def test_supported_charsets_do_not_block(charset: str) -> None:
    inv = SchemaInventory(columns=[_column(charset=charset)])
    assert "BLOCKER-8" not in _ids(_assess(inv).blockers)


def test_case_collision_blocks_when_source_is_case_sensitive() -> None:
    inv = SchemaInventory(tables=[_table("Orders"), _table("orders")])
    assert _finding(_assess(inv, _meta(lower_case_table_names=0)), "BLOCKER-9").count == 1


def test_case_collision_impossible_when_source_already_folds_case() -> None:
    inv = SchemaInventory(tables=[_table("Orders"), _table("orders")])
    assert "BLOCKER-9" not in _ids(_assess(inv, _meta(lower_case_table_names=2)).blockers)


def test_case_collision_action_explains_it_is_unfixable_on_cloudsql() -> None:
    inv = SchemaInventory(tables=[_table("Orders"), _table("orders")])
    action = _finding(_assess(inv), "BLOCKER-9").action
    assert "cannot fix this by changing the source" in action


def test_query_log_blockers_default_to_not_detected() -> None:
    ids = _ids(_assess().blockers)
    assert not {"BLOCKER-5", "BLOCKER-6", "BLOCKER-7"} & ids


def test_query_log_blockers_fire_when_signals_supplied() -> None:
    signals = QueryLogSignals(xa_detected=True, udf_count=3, xml_function_detected=True)
    ids = _ids(_assess(query_log=signals).blockers)
    assert {"BLOCKER-5", "BLOCKER-6", "BLOCKER-7"} <= ids


def test_fulltext_warns_off_starter_only() -> None:
    inv = SchemaInventory(
        indexes=[IndexInfo(schema_name="s", table_name="t", index_name="ft", index_type="FULLTEXT", is_unique=False)]
    )
    assert "WARNING-2" in _ids(_assess(inv, tier="essential").warnings)
    assert "WARNING-2" not in _ids(_assess(inv, tier="starter").warnings)


def test_updatable_view_warns() -> None:
    inv = SchemaInventory(views=[ViewInfo(schema_name="s", view_name="v", is_updatable=True)])
    assert _finding(_assess(inv), "WARNING-9").count == 1


# --- Cloud SQL platform rules ---------------------------------------------


def test_source_read_replica_blocks_only_when_replicating() -> None:
    meta = _meta(is_replica=True)
    assert "CSQL-BLOCKER-1" not in _ids(_assess(metadata=meta).blockers)
    assert "CSQL-BLOCKER-1" in _ids(
        _assess(metadata=meta, continue_replication_planned=True).blockers
    )


def test_iam_users_warn() -> None:
    meta = _meta(iam_users=["svc@p.iam@%", "dev@x.com@%"], iam_authentication_enabled=True)
    assert _finding(_assess(metadata=meta), "CSQL-WARNING-1").count == 2


def test_iam_flag_alone_still_warns() -> None:
    meta = _meta(iam_authentication_enabled=True)
    assert _finding(_assess(metadata=meta), "CSQL-WARNING-1").count == 1


def test_foreign_definers_warn() -> None:
    inv = SchemaInventory(definer_objects=["view s.v", "procedure s.p"])
    assert _finding(_assess(inv), "CSQL-WARNING-2").count == 2


def test_mysql_57_warns() -> None:
    assert "CSQL-WARNING-3" in _ids(_assess(metadata=_meta(mysql_version="5.7.44-google")).warnings)
    assert "CSQL-WARNING-3" not in _ids(_assess(metadata=_meta(mysql_version="8.0.37")).warnings)


def test_lax_sql_mode_warns_with_count_of_missing_modes() -> None:
    meta = _meta(sql_mode="ONLY_FULL_GROUP_BY,NO_ENGINE_SUBSTITUTION")
    # Missing STRICT_TRANS_TABLES, NO_ZERO_IN_DATE, NO_ZERO_DATE, ERROR_FOR_DIVISION_BY_ZERO
    assert _finding(_assess(metadata=meta), "CSQL-WARNING-4").count == 4


def test_stricter_source_sql_mode_does_not_warn() -> None:
    # Only the lax direction is dangerous; extra strictness on the source is fine.
    meta = _meta(sql_mode=STRICT_SQL_MODE + ",ANSI_QUOTES,PIPES_AS_CONCAT")
    assert "CSQL-WARNING-4" not in _ids(_assess(metadata=meta).warnings)


def test_unknown_sql_mode_does_not_warn() -> None:
    assert "CSQL-WARNING-4" not in _ids(_assess(metadata=_meta(sql_mode=None)).warnings)


def test_cloudsql_system_artifacts_warn() -> None:
    meta = _meta(has_heartbeat_table=True, cloudsql_system_users=["cloudsqladmin"])
    assert _finding(_assess(metadata=meta), "CSQL-WARNING-5").count == 2


def test_downstream_replicas_warn() -> None:
    meta = _meta(connected_replica_count=2, connected_replica_hosts=["a", "b"])
    assert _finding(_assess(metadata=meta), "CSQL-WARNING-6").count == 2


def test_non_innodb_tables_warn() -> None:
    inv = SchemaInventory(non_innodb_tables=["access_log", "session_cache"])
    assert _finding(_assess(inv), "CSQL-WARNING-7").count == 2


# --- binlog gating --------------------------------------------------------


def _failing_binlog(rule_id: str) -> BinlogPrecheckResult:
    return BinlogPrecheckResult(
        checks=[
            BinlogVariableCheck(
                variable="v", rule_id=rule_id, actual="x", required="y", status="fail", why="z"
            )
        ],
        continue_replication_ready=False,
    )


def test_binlog_rules_do_not_fire_for_cutover_only_migrations() -> None:
    result = _assess(binlog=_failing_binlog("CSQL-WARNING-8"), continue_replication_planned=False)
    assert "CSQL-WARNING-8" not in _ids(result.warnings)


def test_binlog_rules_fire_when_replication_is_planned() -> None:
    result = _assess(binlog=_failing_binlog("CSQL-WARNING-8"), continue_replication_planned=True)
    assert "CSQL-WARNING-8" in _ids(result.warnings)


def test_compatible_features_always_listed() -> None:
    result = _assess()
    assert result.compatible
    assert any("Foreign keys" in f for f in result.compatible)
