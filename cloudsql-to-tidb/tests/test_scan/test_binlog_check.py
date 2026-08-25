from __future__ import annotations

from tishift_cloudsql.core.scan.analyzers.binlog_check import evaluate_binlog_config

PASSING = {
    "log_bin": "ON",
    "binlog_format": "ROW",
    "binlog_row_image": "FULL",
    "binlog_expire_logs_seconds": "604800",
    "binlog_transaction_compression": "OFF",
    "binlog_row_value_options": "",
    "server_id": "12345",
    "gtid_mode": "ON",
    "enforce_gtid_consistency": "ON",
}


def _check(result, variable):
    return next(c for c in result.checks if c.variable == variable)


def test_fully_configured_source_is_ready() -> None:
    result = evaluate_binlog_config(PASSING)
    assert result.continue_replication_ready
    assert all(c.status in ("pass", "info") for c in result.checks)


def test_log_bin_off_fails() -> None:
    result = evaluate_binlog_config({**PASSING, "log_bin": "OFF"})
    assert not result.continue_replication_ready
    assert _check(result, "log_bin").status == "fail"


def test_log_bin_remediation_is_the_instance_flag_not_set_global() -> None:
    # `SET GLOBAL` fails with ERROR 1227 on Cloud SQL, and log_bin is not a
    # database flag either — it is an instance setting.
    check = _check(evaluate_binlog_config({**PASSING, "log_bin": "OFF"}), "log_bin")
    assert "--enable-bin-log" in check.remediation
    assert "SET GLOBAL" not in check.remediation


def test_binlog_format_remediation_says_not_configurable() -> None:
    # Cloud SQL forces ROW itself; telling someone to patch the flag is wrong.
    check = _check(evaluate_binlog_config({**PASSING, "binlog_format": "STATEMENT"}), "binlog_format")
    assert "Not user-configurable" in check.remediation


def test_binlog_row_image_remediation_uses_database_flags() -> None:
    check = _check(evaluate_binlog_config({**PASSING, "binlog_row_image": "MINIMAL"}), "binlog_row_image")
    assert "--database-flags=binlog_row_image=full" in check.remediation
    assert "REPLACES" in check.remediation





def test_partial_json_fails() -> None:
    result = evaluate_binlog_config({**PASSING, "binlog_row_value_options": "PARTIAL_JSON"})
    assert not result.continue_replication_ready
    check = _check(result, "binlog_row_value_options")
    assert check.status == "fail"
    assert "silent corruption" in check.why


def test_partial_json_remediation_explains_the_replace_semantics() -> None:
    check = _check(
        evaluate_binlog_config({**PASSING, "binlog_row_value_options": "PARTIAL_JSON"}),
        "binlog_row_value_options",
    )
    assert "--clear-database-flags" in check.remediation


def test_transaction_compression_on_fails() -> None:
    result = evaluate_binlog_config({**PASSING, "binlog_transaction_compression": "ON"})
    assert not result.continue_replication_ready


def test_missing_variable_fails_rather_than_passing_silently() -> None:
    variables = {k: v for k, v in PASSING.items() if k != "binlog_row_image"}
    result = evaluate_binlog_config(variables)
    assert _check(result, "binlog_row_image").status == "fail"
    assert _check(result, "binlog_row_image").actual is None


def test_zero_server_id_warns_but_does_not_gate() -> None:
    result = evaluate_binlog_config({**PASSING, "server_id": "0"})
    assert result.continue_replication_ready  # informational only
    assert _check(result, "server_id").status == "warn"


def test_gtid_variables_are_informational() -> None:
    result = evaluate_binlog_config(PASSING)
    assert _check(result, "gtid_mode").status == "info"
    assert _check(result, "gtid_mode").rule_id is None
    assert _check(result, "enforce_gtid_consistency").status == "info"


def test_passing_checks_carry_no_remediation_noise() -> None:
    assert _check(evaluate_binlog_config(PASSING), "log_bin").remediation == ""


def test_case_and_whitespace_are_tolerated() -> None:
    result = evaluate_binlog_config({**PASSING, "log_bin": " on ", "binlog_format": "row"})
    assert result.continue_replication_ready

def test_retention_is_informational_not_gated() -> None:
    # Verified on a live instance: transactionLogRetentionDays was raised 1 -> 7
    # while binlog_expire_logs_seconds stayed at 86400 and was not even present
    # in the instance's databaseFlags. Gating on this variable would WARN
    # forever on a correctly configured instance.
    result = evaluate_binlog_config({**PASSING, "binlog_expire_logs_seconds": "60"})
    assert result.continue_replication_ready
    check = _check(result, "binlog_expire_logs_seconds")
    assert check.status == "info"
    assert check.rule_id is None
    assert "transactionLogRetentionDays" in check.why


def test_retention_check_points_at_gcloud_for_the_real_value() -> None:
    check = _check(evaluate_binlog_config(PASSING), "binlog_expire_logs_seconds")
    assert "gcloud sql instances describe" in check.why
