"""Renderers for the `gcloud` commands quoted in reports and guides.

Pure string building — nothing here shells out, and nothing here needs
credentials. It exists because every remediation this toolkit emits has to be
a Cloud SQL command rather than a SQL statement: `SET GLOBAL` fails with
ERROR 1227 on Cloud SQL for *every* variable the migration cares about, since
no user (not even one with `cloudsqlsuperuser`) holds SUPER.

When gcp.* is unset in the config the renderers fall back to angle-bracket
placeholders, so the output is still copy-pasteable-after-editing rather than
silently wrong.

Flag behaviour verified against the gcloud reference:
https://cloud.google.com/sdk/gcloud/reference/sql/instances/patch
"""

from __future__ import annotations

from tishift_cloudsql.config import GcpConfig

# `--database-flags` REPLACES the instance's entire flag list rather than
# merging into it ("The value given for this argument replaces the existing
# list"). Every rendered patch command therefore has to restate the flags the
# instance already has, or they are silently cleared. This constant is appended
# to every patch command so the warning travels with the command itself.
PATCH_REPLACE_WARNING = (
    "--database-flags replaces the instance's entire flag list. "
    "Run `gcloud sql instances describe <INSTANCE> --format='value(settings.databaseFlags)'` "
    "first and restate every flag you want to keep."
)


def _instance(config: GcpConfig) -> str:
    return config.instance or "<INSTANCE>"


def _project_flag(config: GcpConfig) -> str:
    return f" --project={config.project}" if config.project else ""


def describe_flags_command(config: GcpConfig) -> str:
    """Command that dumps the instance's current flag list.

    Always run before a patch, because of the replace-not-merge behaviour.
    """
    return (
        f"gcloud sql instances describe {_instance(config)}{_project_flag(config)} "
        "--format='value(settings.databaseFlags)'"
    )


def patch_flags_command(config: GcpConfig, flags: dict[str, str]) -> str:
    """Render a `gcloud sql instances patch --database-flags=...` command."""
    rendered = ",".join(f"{name}={value}" for name, value in flags.items())
    return (
        f"gcloud sql instances patch {_instance(config)}{_project_flag(config)} "
        f"--database-flags={rendered}"
    )


def clear_flag_command(config: GcpConfig, flag: str) -> str:
    """Render the command that removes a single flag.

    There is no "unset one flag" verb. Because the list is replaced wholesale,
    a flag is cleared by re-issuing --database-flags *without* it — or by
    --clear-database-flags when no other flags are set on the instance.
    """
    return (
        f"# Remove {flag}: re-issue --database-flags listing every flag EXCEPT {flag}.\n"
        f"# If {flag} is the only flag set on the instance:\n"
        f"gcloud sql instances patch {_instance(config)}{_project_flag(config)} "
        "--clear-database-flags"
    )


def enable_binlog_command(config: GcpConfig, retention_days: int = 7) -> str:
    """Render the command that turns on binary logging and sets its retention.

    Binary logging on Cloud SQL for MySQL is `--enable-bin-log`, not a database
    flag, and it requires automatic backups to already be on ("Must have
    automatic backups enabled to use"). `--retained-transaction-log-days`
    accepts 1-35, but the ceiling depends on the instance's edition, so a value
    that is accepted on Enterprise Plus may be rejected on Enterprise.
    """
    return (
        f"gcloud sql instances patch {_instance(config)}{_project_flag(config)} "
        f"--enable-bin-log --retained-transaction-log-days={retention_days}\n"
        "# If automatic backups are off, enable them in the same or a prior patch:\n"
        f"#   gcloud sql instances patch {_instance(config)}{_project_flag(config)} "
        "--backup-start-time=03:00"
    )


def export_sql_command(config: GcpConfig, database: str, object_name: str = "dump.sql.gz") -> str:
    """Render a logical (mysqldump-based) export to GCS.

    Single-threaded and single-file: fine up to a few tens of GiB, past which
    the CSV or Dumpling paths win. `--offload` runs the export on a temporary
    instance so the primary is not strained.
    """
    bucket = config.export_bucket or "gs://<BUCKET>"
    return (
        f"gcloud sql export sql {_instance(config)} {bucket}/{object_name}"
        f"{_project_flag(config)} --database={database} --offload"
    )


def export_csv_command(
    config: GcpConfig, database: str, query: str, object_name: str = "table.csv"
) -> str:
    """Render a per-table CSV export to GCS — the input format TiDB Cloud imports."""
    bucket = config.export_bucket or "gs://<BUCKET>"
    return (
        f"gcloud sql export csv {_instance(config)} {bucket}/{object_name}"
        f"{_project_flag(config)} --database={database} --query=\"{query}\" --offload"
    )


def grant_bucket_access_commands(config: GcpConfig) -> str:
    """Render the IAM grant the export needs.

    The export is performed by the *instance's* service account, not by the
    caller, so the caller having bucket access is not sufficient — this is the
    single most common reason a first `gcloud sql export` fails.
    """
    bucket = config.export_bucket or "gs://<BUCKET>"
    return (
        f"SA=$(gcloud sql instances describe {_instance(config)}{_project_flag(config)} "
        "--format='value(serviceAccountEmailAddress)')\n"
        f"gcloud storage buckets add-iam-policy-binding {bucket} "
        '--member="serviceAccount:$SA" --role="roles/storage.objectAdmin"'
    )
