from __future__ import annotations

from tishift_cloudsql.config import GcpConfig
from tishift_cloudsql.gcloud import (
    PATCH_REPLACE_WARNING,
    clear_flag_command,
    describe_flags_command,
    enable_binlog_command,
    export_csv_command,
    export_sql_command,
    grant_bucket_access_commands,
    patch_flags_command,
)

FULL = GcpConfig(
    project="demo-project",
    instance="demo-instance",
    region="us-central1",
    export_bucket="gs://demo-bucket",
)
EMPTY = GcpConfig()


def test_patch_command_renders_flags_and_project() -> None:
    cmd = patch_flags_command(FULL, {"binlog_row_image": "full"})
    assert cmd == (
        "gcloud sql instances patch demo-instance --project=demo-project "
        "--database-flags=binlog_row_image=full"
    )


def test_patch_command_joins_multiple_flags() -> None:
    cmd = patch_flags_command(FULL, {"a": "1", "b": "2"})
    assert "--database-flags=a=1,b=2" in cmd


def test_placeholders_used_when_config_is_empty() -> None:
    cmd = patch_flags_command(EMPTY, {"x": "1"})
    assert "<INSTANCE>" in cmd
    assert "--project" not in cmd


def test_replace_warning_is_explicit() -> None:
    # --database-flags replaces the whole list; omitting an existing flag
    # silently clears it. This is the single most damaging gcloud footgun here.
    assert "replaces" in PATCH_REPLACE_WARNING
    assert "describe" in PATCH_REPLACE_WARNING


def test_describe_command_dumps_current_flags() -> None:
    assert "settings.databaseFlags" in describe_flags_command(FULL)


def test_clear_flag_command_explains_there_is_no_unset_verb() -> None:
    cmd = clear_flag_command(FULL, "binlog_row_value_options")
    assert "--clear-database-flags" in cmd
    assert "EXCEPT binlog_row_value_options" in cmd


def test_enable_binlog_is_an_instance_setting_not_a_flag() -> None:
    cmd = enable_binlog_command(FULL, retention_days=7)
    assert "--enable-bin-log" in cmd
    assert "--retained-transaction-log-days=7" in cmd
    assert "--database-flags" not in cmd
    # Binary logging requires automatic backups to already be on.
    assert "--backup-start-time" in cmd


def test_export_sql_uses_offload() -> None:
    cmd = export_sql_command(FULL, "myapp")
    assert cmd.startswith("gcloud sql export sql demo-instance gs://demo-bucket/dump.sql.gz")
    assert "--database=myapp" in cmd
    assert "--offload" in cmd


def test_export_csv_includes_query() -> None:
    cmd = export_csv_command(FULL, "myapp", "SELECT * FROM orders", "orders.csv")
    assert "gs://demo-bucket/orders.csv" in cmd
    assert '--query="SELECT * FROM orders"' in cmd


def test_export_falls_back_to_bucket_placeholder() -> None:
    assert "gs://<BUCKET>" in export_sql_command(EMPTY, "myapp")


def test_bucket_grant_targets_the_instance_service_account() -> None:
    # The export runs as the instance, not as the caller — the most common
    # reason a first export fails.
    cmd = grant_bucket_access_commands(FULL)
    assert "serviceAccountEmailAddress" in cmd
    assert "roles/storage.objectAdmin" in cmd
    assert "gs://demo-bucket" in cmd
