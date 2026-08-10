from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tishift_cloudsql.cli import main
from tests.test_scan.fake_connection import ScriptedConnection
from tests.test_scan.test_orchestrator import ALL_RESPONSES

SAMPLE_SCHEMA = Path(__file__).parent.parent / "sql" / "sample-schema.sql"


@pytest.fixture()
def fake_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tishift_cloudsql.connection.connect_source",
        lambda cfg, read_only=True: ScriptedConnection(list(ALL_RESPONSES)),
    )


# --- stubs ----------------------------------------------------------------


def test_load_is_disabled_and_exits_two() -> None:
    result = CliRunner().invoke(main, ["load"])
    assert result.exit_code == 2
    assert "intentionally disabled" in result.output
    assert "docs/load-guide.md" in result.output


@pytest.mark.parametrize("command,guide", [("check", "docs/check-guide.md"), ("sync", "docs/sync-guide.md")])
def test_stubs_exit_two_and_name_their_guide(command: str, guide: str) -> None:
    # Non-zero so CI cannot mistake a stub for a completed phase.
    result = CliRunner().invoke(main, [command])
    assert result.exit_code == 2
    assert guide in result.output


# --- scan -----------------------------------------------------------------


def test_scan_writes_reports(fake_source, sample_config_path: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["scan", "--config", str(sample_config_path), "--format", "json", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    report = json.loads((tmp_path / "tishift-cloudsql-report.json").read_text())
    assert report["schema"] == "myapp"
    assert report["tier"] == "essential"


def test_scan_prints_cli_summary(fake_source, sample_config_path: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["scan", "--config", str(sample_config_path), "--format", "cli", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "Cloud SQL for MySQL Scan Report" in result.output
    assert "Readiness Score" in result.output


def test_scan_quiet_suppresses_summary(fake_source, sample_config_path: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["scan", "--config", str(sample_config_path), "--format", "cli", "--quiet", "--output-dir", str(tmp_path)],
    )
    assert "Readiness Score" not in result.output


def test_scan_warns_that_dm_cannot_use_the_auth_proxy(
    fake_source, sample_config_path: Path, tmp_path: Path
) -> None:
    result = CliRunner().invoke(
        main,
        [
            "scan",
            "--config", str(sample_config_path),
            "--continue-replication",
            "--format", "json",
            "--output-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "DM cannot connect through the Cloud SQL Auth Proxy" in result.output


def test_scan_does_not_warn_without_continue_replication(
    fake_source, sample_config_path: Path, tmp_path: Path
) -> None:
    result = CliRunner().invoke(
        main,
        ["scan", "--config", str(sample_config_path), "--format", "json", "--output-dir", str(tmp_path)],
    )
    assert "Auth Proxy" not in result.output


def test_scan_notes_unsupported_format(fake_source, sample_config_path: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["scan", "--config", str(sample_config_path), "--format", "pdf", "--output-dir", str(tmp_path)],
    )
    assert "unsupported format(s) skipped: pdf" in result.output


def test_scan_reports_missing_config_cleanly() -> None:
    result = CliRunner().invoke(main, ["scan", "--config", "nope.yaml"])
    assert result.exit_code != 0
    assert "Config file not found" in result.output
    assert "Traceback" not in result.output


def test_scan_reports_invalid_config_cleanly(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("source: {host: h, user: u, database: d}\ntarget: {host: t, user: u, database: d, tier: gold}\n")
    result = CliRunner().invoke(main, ["scan", "--config", str(bad)])
    assert result.exit_code != 0
    assert "Invalid config" in result.output


# --- convert --------------------------------------------------------------


def test_convert_requires_a_ddl_file(sample_config_path: Path) -> None:
    result = CliRunner().invoke(main, ["convert", "--config", str(sample_config_path)])
    assert result.exit_code != 0
    assert "--ddl-file is required" in result.output


def test_convert_writes_schema_and_reports(sample_config_path: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["convert", "--ddl-file", str(SAMPLE_SCHEMA), "--config", str(sample_config_path), "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "converted-schema.sql").exists()
    assert (tmp_path / "ddl-cleanup-report.json").exists()
    assert (tmp_path / "ddl-cleanup-report.md").exists()


def test_convert_dry_run_writes_nothing(sample_config_path: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["convert", "--ddl-file", str(SAMPLE_SCHEMA), "--config", str(sample_config_path), "--dry-run", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "converted-schema.sql").exists()
    assert "---" in result.output  # a unified diff


def test_convert_takes_tier_from_config(sample_config_path: Path, tmp_path: Path) -> None:
    CliRunner().invoke(
        main,
        ["convert", "--ddl-file", str(SAMPLE_SCHEMA), "--config", str(sample_config_path), "--output-dir", str(tmp_path)],
    )
    report = json.loads((tmp_path / "ddl-cleanup-report.json").read_text())
    assert report["tier"] == "essential"


def test_convert_reminds_about_foreign_key_checks(sample_config_path: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["convert", "--ddl-file", str(SAMPLE_SCHEMA), "--config", str(sample_config_path), "--output-dir", str(tmp_path)],
    )
    assert "SET FOREIGN_KEY_CHECKS=0" in result.output


def test_sample_schema_triggers_every_ddl_rule(sample_config_path: Path, tmp_path: Path) -> None:
    # The fixture exists to keep every rule exercised end to end; if a rule
    # stops firing, either the rule or the fixture has drifted.
    CliRunner().invoke(
        main,
        ["convert", "--ddl-file", str(SAMPLE_SCHEMA), "--config", str(sample_config_path), "--output-dir", str(tmp_path)],
    )
    summary = json.loads((tmp_path / "ddl-cleanup-report.json").read_text())["summary"]
    unfired = [rid for rid, s in summary.items() if s["count"] == 0]
    assert unfired == [], f"sample-schema.sql no longer triggers: {unfired}"


def test_convert_on_sample_schema_has_no_parse_errors(sample_config_path: Path, tmp_path: Path) -> None:
    CliRunner().invoke(
        main,
        ["convert", "--ddl-file", str(SAMPLE_SCHEMA), "--config", str(sample_config_path), "--output-dir", str(tmp_path)],
    )
    report = json.loads((tmp_path / "ddl-cleanup-report.json").read_text())
    assert report["parse_errors"] == []


def test_convert_is_idempotent_end_to_end(sample_config_path: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    args = ["--config", str(sample_config_path)]
    CliRunner().invoke(main, ["convert", "--ddl-file", str(SAMPLE_SCHEMA), *args, "--output-dir", str(first)])
    CliRunner().invoke(
        main,
        ["convert", "--ddl-file", str(first / "converted-schema.sql"), *args, "--output-dir", str(second)],
    )
    report = json.loads((second / "ddl-cleanup-report.json").read_text())
    assert report["tiflash_statements"] == []
    rewrites = [f for f in report["findings"] if f["action_taken"] in ("rewritten", "commented_out")]
    assert rewrites == []
