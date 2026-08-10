"""TiShift Cloud SQL CLI — entry point for the tishift-cloudsql command.

Every heavy import (pymysql, sqlglot, the core modules) is function-local so
`--help` stays fast and the CLI imports cleanly on a machine with no database
driver installed.
"""

import click

from tishift_cloudsql import __version__


@click.group()
@click.version_option(version=__version__, prog_name="tishift-cloudsql")
def main() -> None:
    """Google Cloud SQL for MySQL to TiDB migration toolkit."""


def _load_config_or_fail(config_path: str):
    """Load and validate the YAML config, converting the usual failure modes
    (missing file, invalid YAML, schema errors) into clean CLI errors instead
    of tracebacks."""
    from pathlib import Path

    from pydantic import ValidationError

    from tishift_cloudsql.config import load_config

    try:
        return load_config(Path(config_path))
    except FileNotFoundError:
        raise click.ClickException(f"Config file not found: {config_path}") from None
    except ValidationError as exc:
        raise click.ClickException(f"Invalid config {config_path}:\n{exc}") from None


REPORT_FORMATS = ("cli", "json", "md")
DEFAULT_CONFIG = "tishift-cloudsql.yaml"


@main.command()
@click.option("--config", default=DEFAULT_CONFIG, help="Path to config file.")
@click.option(
    "--database", default=None, help="Schema to scan (default: source.database from config)."
)
@click.option(
    "--continue-replication",
    "continue_replication_planned",
    is_flag=True,
    help=(
        "Include continue-replication (TiDB Cloud DM) readiness checks — binlog rules "
        "and the valid-indexes precheck — in the findings and the score."
    ),
)
@click.option(
    "--no-network-path",
    is_flag=True,
    help="Score as if no confirmed network path to the source exists.",
)
@click.option(
    "--format",
    "formats",
    multiple=True,
    default=(),
    help="Output format(s): cli, json, md (default: output.formats from config).",
)
@click.option(
    "--output-dir", default=None, help="Report output directory (default: output.dir from config)."
)
@click.option(
    "--quiet", is_flag=True, help="Suppress the CLI text summary (files are still written)."
)
def scan(
    config: str,
    database: str | None,
    continue_replication_planned: bool,
    no_network_path: bool,
    formats: tuple[str, ...],
    output_dir: str | None,
    quiet: bool,
) -> None:
    """Scan a Cloud SQL for MySQL instance and produce a readiness report.

    Connects to the source read-only, runs every collector (platform metadata,
    IAM/system users, replication topology, schema inventory, binlog precheck,
    and — with --continue-replication — the valid-indexes precheck), applies the
    compatibility rules and readiness scoring, and prints/writes the result.
    """
    from pathlib import Path

    import pymysql

    from tishift_cloudsql.connection import connect_source
    from tishift_cloudsql.core.scan.orchestrator import run_scan
    from tishift_cloudsql.core.scan.report import build_report, render_cli, write_reports

    cfg = _load_config_or_fail(config)
    schema = database or cfg.source.database

    formats = formats or tuple(cfg.output.formats)
    formats = tuple("md" if f == "markdown" else f for f in formats)
    unsupported = sorted(set(formats) - set(REPORT_FORMATS))
    if unsupported:
        click.echo(
            f"Note: unsupported format(s) skipped: {', '.join(unsupported)} "
            f"(supported: {', '.join(REPORT_FORMATS)})."
        )

    try:
        conn = connect_source(cfg.source)
    except pymysql.Error as exc:
        raise click.ClickException(f"Could not connect to source: {exc}") from None

    try:
        result = run_scan(
            conn,
            schema,
            tier=cfg.target.tier,
            continue_replication_planned=continue_replication_planned,
            network_path_confirmed=not no_network_path,
            export_bucket_configured=bool(cfg.gcp.export_bucket),
        )
    finally:
        conn.close()

    report = build_report(result)

    if not quiet and "cli" in formats:
        click.echo(render_cli(report))

    # The Auth Proxy is fine for scanning and completely unusable by DM. Saying
    # so here, at the moment the user asks for replication checks, is far
    # cheaper than discovering it at cutover.
    if continue_replication_planned and cfg.source.connection_method == "auth_proxy":
        click.echo(
            "⚠️  source.connection_method is auth_proxy. TiDB Cloud DM cannot connect "
            "through the Cloud SQL Auth Proxy — it is a local client-side connector. "
            "Plan a public IP with authorized networks, or private IP with VPC peering / "
            "Private Service Connect, before Phase 7. See docs/sync-guide.md."
        )

    out_dir = Path(output_dir or cfg.output.dir)
    written = write_reports(report, out_dir, formats)
    for fmt, path in written.items():
        click.echo(f"Wrote {fmt}: {path}")


@main.command()
@click.option(
    "--ddl-file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="SQL file with CREATE TABLE statements (mysqldump / SHOW CREATE TABLE output).",
)
@click.option(
    "--config", default=DEFAULT_CONFIG, help="Path to config file (source of --tier when omitted)."
)
@click.option(
    "--tier",
    default=None,
    help="Target TiDB tier: starter, essential, dedicated, self-hosted (default: target.tier).",
)
@click.option(
    "--tiflash-replicas",
    default=2,
    show_default=True,
    help="TiFlash replica count emitted after each FULLTEXT table's CREATE TABLE (0 to disable).",
)
@click.option("--dry-run", is_flag=True, help="Print a diff and summary without writing files.")
@click.option("--output-dir", default="./tishift-reports", help="Output directory.")
def convert(
    ddl_file: str | None,
    config: str,
    tier: str | None,
    tiflash_replicas: int,
    dry_run: bool,
    output_dir: str,
) -> None:
    """Convert Cloud SQL for MySQL schema DDL to TiDB-compatible DDL.

    DEFINER clauses, non-InnoDB engines, storage-only table options, and
    utf8/utf8mb3 charsets are rewritten or commented out (CSQL-DDL-1..7).
    Nothing is deleted — the original text is preserved in TISHIFT-REMOVED
    comments — and re-running convert over its own output is a no-op.
    """
    import difflib
    from pathlib import Path

    from tishift_cloudsql.core.convert.report import build_report, write_reports
    from tishift_cloudsql.core.convert.schema_transformer import transform_schema

    if ddl_file is None:
        raise click.UsageError("--ddl-file is required.")

    if tier is None:
        tier = _load_config_or_fail(config).target.tier

    original = Path(ddl_file).read_text()
    result = transform_schema(original, tier=tier, tiflash_replicas=tiflash_replicas)
    report = build_report(result, ddl_file, tier, tiflash_replicas)

    if dry_run:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            result.sql.splitlines(keepends=True),
            fromfile=ddl_file,
            tofile="converted-schema.sql",
        )
        click.echo("".join(diff))
    else:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        schema_path = out_dir / "converted-schema.sql"
        schema_path.write_text(result.sql)
        json_path, md_path = write_reports(result, out_dir, ddl_file, tier, tiflash_replicas)
        click.echo(f"Wrote {schema_path}")
        click.echo(f"Wrote {json_path}")
        click.echo(f"Wrote {md_path}")

    click.echo("")
    # Zero-hit rules are hidden while any rule matched; if nothing matched, all
    # rules are listed (0 hits) so the output shows what was checked.
    summary = report["summary"]
    display = {rid: s for rid, s in summary.items() if s["count"]} or summary
    for rule_id, s in display.items():
        click.echo(f"{rule_id}: {s['count']} hit(s) — {s['description']}")
    click.echo(
        f"FULLTEXT tables (CSQL-DDL-5): {len(result.fulltext_tables)}; "
        f"spatial tables (CSQL-DDL-6): {len(result.spatial_tables)}; "
        f"TiFlash statements emitted: {len(result.tiflash_statements)}"
    )
    review_count = sum(1 for f in result.findings if f.risk in ("assess", "blocker"))
    if review_count:
        click.echo(f"⚠️  {review_count} finding(s) need manual review (see report).")
    click.echo(
        "Apply the output wrapped in SET FOREIGN_KEY_CHECKS=0 / =1 — per-table DDL is not "
        "FK-topologically ordered and will otherwise fail with ERROR 1824."
    )
    if result.parse_errors:
        click.echo(f"❌ {len(result.parse_errors)} statement(s) failed re-parse after cleanup:")
        for err in result.parse_errors:
            click.echo(f"   {err}")
        raise SystemExit(1)


def _not_implemented(command: str, guide: str) -> None:
    """Stub exit for phases that are documented but not automated yet.

    Exits non-zero so scripts and CI cannot mistake the stub for a completed
    phase."""
    click.echo(f"tishift-cloudsql {command} is not implemented yet — follow {guide} manually.")
    raise SystemExit(2)


@main.command()
@click.option("--config", default=DEFAULT_CONFIG, help="Path to config file.")
@click.option("--strategy", default="auto", help="Load strategy: auto, gcs, dumpling, lightning.")
def load(config: str, strategy: str) -> None:
    """Load data into TiDB — intentionally disabled.

    Bulk data movement is deliberately excluded from this tool: it is the one
    irreversible, hours-long step, and a half-finished load leaves a target that
    looks populated but is not. docs/load-guide.md covers the manual path.
    """
    click.echo(
        "tishift-cloudsql load is intentionally disabled — data loading is a high-stakes "
        "step this tool does not handle. Complete it independently by following "
        "docs/load-guide.md (gcloud sql export to GCS, then TiDB Cloud import)."
    )
    raise SystemExit(2)


@main.command()
@click.option("--config", default=DEFAULT_CONFIG, help="Path to config file.")
@click.option("--output", default="cli,json", help="Output format(s).")
@click.option("--checksum", is_flag=True, help="Enable checksum validation.")
def check(config: str, output: str, checksum: bool) -> None:
    """Validate data integrity between source and target.

    Not implemented yet — docs/check-guide.md covers the manual path (row
    counts, structure diff, checksums, TiFlash replica availability).
    """
    _not_implemented("check", "docs/check-guide.md")


@main.command()
@click.option("--config", default=DEFAULT_CONFIG, help="Path to config file.")
def sync(config: str) -> None:
    """Run continue-replication prechecks for a Cloud SQL -> TiDB DM migration.

    This command would only verify preconditions (grants, binlog settings,
    PK/unique-index coverage, network reachability). The DM task itself is
    created, started, monitored, and stopped in the TiDB Cloud console.

    Not implemented yet — docs/sync-guide.md covers the manual prechecks.
    """
    _not_implemented("sync", "docs/sync-guide.md")


if __name__ == "__main__":
    main()
