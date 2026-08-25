from __future__ import annotations

from tishift_cloudsql.core.scan.orchestrator import run_scan
from tests.test_scan.fake_connection import ScriptedConnection
from tests.test_scan.test_platform_collector import BASE_RESPONSES as PLATFORM_RESPONSES
from tests.test_scan.test_schema_collector import BASE_RESPONSES as SCHEMA_RESPONSES

BINLOG_RESPONSES: list[tuple[str, object]] = [
    (
        "SHOW VARIABLES",
        [
            {"Variable_name": "log_bin", "Value": "ON"},
            {"Variable_name": "binlog_format", "Value": "ROW"},
            {"Variable_name": "binlog_row_image", "Value": "FULL"},
            {"Variable_name": "binlog_expire_logs_seconds", "Value": "604800"},
            {"Variable_name": "binlog_transaction_compression", "Value": "OFF"},
            {"Variable_name": "binlog_row_value_options", "Value": ""},
            {"Variable_name": "server_id", "Value": "42"},
            {"Variable_name": "gtid_mode", "Value": "ON"},
            {"Variable_name": "enforce_gtid_consistency", "Value": "ON"},
        ],
    ),
]

# The valid-indexes query also selects from information_schema.tables, so it
# must be registered before the schema collector's table query or the substring
# match would steal it.
#
# Keys are UPPERCASE because that is what information_schema actually returns,
# whatever case the SELECT used. A lowercase fixture here previously matched the
# collector's bug instead of reality, and the KeyError only showed up against a
# live server.
VALID_INDEX_RESPONSES: list[tuple[str, object]] = [
    ("NOT IN (\n        SELECT", [{"TABLE_SCHEMA": "myapp", "TABLE_NAME": "no_pk"}]),
]

ALL_RESPONSES = [
    *VALID_INDEX_RESPONSES,
    *BINLOG_RESPONSES,
    *PLATFORM_RESPONSES,
    *SCHEMA_RESPONSES,
]


def _conn() -> ScriptedConnection:
    return ScriptedConnection(list(ALL_RESPONSES))


def test_scan_produces_a_complete_result() -> None:
    result = run_scan(_conn(), "myapp", tier="essential")
    assert result.schema == "myapp"
    assert result.tier == "essential"
    assert result.metadata.mysql_version == "8.0.37-google"
    assert len(result.inventory.tables) == 2
    assert result.binlog.continue_replication_ready
    assert 0 <= result.score.overall <= 100


def test_total_size_sums_data_and_index_bytes() -> None:
    result = run_scan(_conn(), "myapp")
    assert result.total_size_bytes == 2048 + 1024 + 512 + 0


def test_valid_index_precheck_skipped_by_default() -> None:
    result = run_scan(_conn(), "myapp")
    assert result.tables_without_valid_index == []


def test_valid_index_precheck_runs_when_replicating() -> None:
    result = run_scan(_conn(), "myapp", continue_replication_planned=True)
    assert result.tables_without_valid_index == ["myapp.no_pk"]


def test_findings_and_score_agree_on_counts() -> None:
    # The scoring engine calls the same rule.check() callables as the
    # compatibility analyzer, so a rule in the findings must have a deduction.
    result = run_scan(_conn(), "myapp", tier="essential")
    finding_ids = {f.rule_id for f in [*result.assessment.blockers, *result.assessment.warnings]}
    deduction_text = " ".join(d for c in result.score.categories for d in c.deductions)
    for rule_id in ("BLOCKER-1", "CSQL-WARNING-7"):
        assert rule_id in finding_ids
        assert rule_id in deduction_text


def test_tier_flows_through_to_fulltext_rule() -> None:
    warnings_essential = {f.rule_id for f in run_scan(_conn(), "myapp", tier="essential").assessment.warnings}
    warnings_starter = {f.rule_id for f in run_scan(_conn(), "myapp", tier="starter").assessment.warnings}
    assert "WARNING-2" in warnings_essential
    assert "WARNING-2" not in warnings_starter


def test_export_bucket_flag_reaches_the_score() -> None:
    with_bucket = run_scan(_conn(), "myapp", export_bucket_configured=True)
    without = run_scan(_conn(), "myapp", export_bucket_configured=False)
    assert without.score.overall < with_bucket.score.overall
