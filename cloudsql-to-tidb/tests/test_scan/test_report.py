from __future__ import annotations

import json
from pathlib import Path

from tishift_cloudsql.core.scan.orchestrator import run_scan
from tishift_cloudsql.core.scan.report import (
    build_report,
    render_cli,
    render_markdown,
    write_reports,
)
from tests.test_scan.fake_connection import ScriptedConnection
from tests.test_scan.test_orchestrator import ALL_RESPONSES


def _report(**kwargs) -> dict:
    result = run_scan(ScriptedConnection(list(ALL_RESPONSES)), "myapp", **kwargs)
    return build_report(result)


def test_report_has_the_documented_top_level_keys() -> None:
    report = _report()
    assert set(report) >= {
        "schema",
        "tier",
        "continue_replication_planned",
        "summary",
        "platform",
        "binlog_precheck",
        "assessment",
        "score",
    }


def test_summary_counts() -> None:
    s = _report()["summary"]
    assert s["table_count"] == 2
    assert s["non_innodb_table_count"] == 1
    assert s["definer_object_count"] == 3
    assert s["stored_procedure_count"] == 1
    assert s["updatable_view_count"] == 1


def test_platform_section_reports_edition_as_unknown() -> None:
    assert _report()["platform"]["edition"] is None


def test_cli_render_includes_score_and_rating() -> None:
    report = _report()
    text = render_cli(report)
    assert f"{report['score']['overall']}/100" in text
    assert report["score"]["rating"] in text


def test_cli_render_shows_remediation_only_for_failures() -> None:
    report = _report()
    for check in report["binlog_precheck"]["checks"]:
        check["status"] = "fail"
        check["remediation"] = "RUN THIS COMMAND"
    assert "fix: RUN THIS COMMAND" in render_cli(report)


def test_cli_render_omits_remediation_when_passing() -> None:
    assert "fix:" not in render_cli(_report())


def test_cli_render_flags_a_read_replica_source() -> None:
    report = _report()
    report["platform"]["is_replica"] = True
    report["platform"]["replica_source_host"] = "10.0.0.9"
    assert "CSQL-BLOCKER-1" in render_cli(report)


def test_cli_render_names_downstream_replicas() -> None:
    report = _report()
    report["platform"]["connected_replica_count"] = 2
    report["platform"]["connected_replica_hosts"] = ["r1", "r2"]
    assert "downstream replica(s)" in render_cli(report)


def test_markdown_render_has_all_sections() -> None:
    md = render_markdown(_report())
    for heading in (
        "# Cloud SQL for MySQL Scan Report",
        "## Summary",
        "## Cloud SQL platform",
        "## Binlog / continue-replication readiness",
        "## Blockers",
        "## Warnings",
        "## Readiness Score",
    ):
        assert heading in md


def test_markdown_distinguishes_not_detected_from_cleared() -> None:
    md = render_markdown(_report())
    assert "## Not detected vs. cleared" in md
    assert "not the same as cleared" in md


def test_markdown_remediation_is_single_line() -> None:
    # Newlines would break the markdown table row.
    report = _report()
    for check in report["binlog_precheck"]["checks"]:
        check["remediation"] = "line one\nline two"
    for line in render_markdown(report).splitlines():
        assert "line one line two" in line or "line one" not in line


def test_write_reports_writes_requested_formats(tmp_path: Path) -> None:
    written = write_reports(_report(), tmp_path, ("json", "md"))
    assert set(written) == {"json", "md"}
    assert (tmp_path / "tishift-cloudsql-report.json").exists()
    assert (tmp_path / "tishift-cloudsql-report.md").exists()


def test_write_reports_skips_cli_only(tmp_path: Path) -> None:
    assert write_reports(_report(), tmp_path, ("cli",)) == {}
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_written_json_is_valid_and_complete(tmp_path: Path) -> None:
    write_reports(_report(), tmp_path, ("json",))
    loaded = json.loads((tmp_path / "tishift-cloudsql-report.json").read_text())
    # The JSON keeps the full binlog rule set even when everything passed,
    # unlike the CLI/markdown renderers which hide non-findings.
    assert len(loaded["binlog_precheck"]["checks"]) >= 6
