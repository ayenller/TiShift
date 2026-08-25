"""Scan report writers — JSON (machine), Markdown (human), CLI (terminal).

`build_report()` assembles one plain dict matching the output format documented
in references/compatibility-rules.md and references/scoring.md; each renderer
then formats that same dict.

Deliberate asymmetry: the CLI and Markdown renderers omit rules that did not
fire, to stay short enough to read. The JSON keeps everything. When someone
asks for a consolidated report, enumerate the full rule set from the JSON, and
say which rules are backed by a real collector and which default to
"not detected".
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from tishift_cloudsql.core.scan.orchestrator import ScanResult

_STATUS_MARKER = {"pass": "✅", "warn": "⚠️ ", "fail": "❌", "info": "ℹ️ "}


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def build_report(result: ScanResult) -> dict:
    inv = result.inventory
    meta = result.metadata

    fk_tables = {c.table_name for c in inv.constraints if c.constraint_type == "FOREIGN KEY"}

    return {
        "schema": result.schema,
        "tier": result.tier,
        "continue_replication_planned": result.continue_replication_planned,
        "summary": {
            "table_count": len(inv.tables),
            "total_size_bytes": result.total_size_bytes,
            "index_count": len(inv.indexes),
            "column_count": len(inv.columns),
            "auto_increment_table_count": len(inv.auto_increment_tables),
            "foreign_key_table_count": len(fk_tables),
            "non_innodb_table_count": len(inv.non_innodb_tables),
            "definer_object_count": len(inv.definer_objects),
            "fk_without_unique_parent_index_count": len(inv.fks_without_unique_parent_index),
            "stored_procedure_count": sum(
                1 for r in inv.routines if r.kind.upper() == "PROCEDURE"
            ),
            "trigger_count": len(inv.triggers),
            "event_count": len(inv.events),
            "view_count": len(inv.views),
            "updatable_view_count": sum(1 for v in inv.views if v.is_updatable),
            "tables_without_valid_index_count": len(result.tables_without_valid_index),
        },
        "platform": {
            "mysql_version": meta.mysql_version,
            "version_comment": meta.version_comment,
            "is_cloudsql": meta.is_cloudsql,
            "edition": meta.edition,
            "sql_mode": meta.sql_mode,
            "lower_case_table_names": meta.lower_case_table_names,
            "iam_authentication_enabled": meta.iam_authentication_enabled,
            "iam_user_count": len(meta.iam_users),
            "cloudsql_system_users": meta.cloudsql_system_users,
            "has_heartbeat_table": meta.has_heartbeat_table,
            "gtid_mode": meta.gtid_mode,
            "read_only": meta.read_only,
            "super_read_only": meta.super_read_only,
            "is_replica": meta.is_replica,
            "replica_source_host": meta.replica_source_host,
            "connected_replica_count": meta.connected_replica_count,
            "connected_replica_hosts": meta.connected_replica_hosts,
        },
        "binlog_precheck": {
            "continue_replication_ready": result.binlog.continue_replication_ready,
            "checks": [asdict(c) for c in result.binlog.checks],
        },
        "tables_without_valid_index": list(result.tables_without_valid_index),
        "definer_objects": list(inv.definer_objects),
        "fks_without_unique_parent_index": list(inv.fks_without_unique_parent_index),
        "non_innodb_tables": list(inv.non_innodb_tables),
        "assessment": {
            "blockers": [asdict(f) for f in result.assessment.blockers],
            "warnings": [asdict(f) for f in result.assessment.warnings],
            "compatible": result.assessment.compatible,
        },
        "score": {
            "overall": result.score.overall,
            "rating": result.score.rating,
            "categories": [asdict(c) for c in result.score.categories],
        },
    }


def _render_topology_line(p: dict) -> str:
    if p["is_replica"]:
        return (
            f"Topology: READ REPLICA (replicating from {p['replica_source_host']}) — "
            f"cannot be the source of a DM task (CSQL-BLOCKER-1)"
        )
    if p["connected_replica_count"]:
        return (
            f"Topology: primary, {p['connected_replica_count']} downstream replica(s) "
            f"({', '.join(p['connected_replica_hosts'])})"
        )
    return "Topology: standalone (no replicas detected)"


def render_cli(report: dict) -> str:
    s = report["summary"]
    p = report["platform"]
    lines = [
        "=== Cloud SQL for MySQL Scan Report ===",
        f"Schema: {report['schema']}   Target tier: {report['tier']}   "
        f"Continue replication planned: {'yes' if report['continue_replication_planned'] else 'no'}",
        "",
        "-- Summary --",
        f"Tables: {s['table_count']}   Total size: {_human_bytes(s['total_size_bytes'])}   "
        f"Indexes: {s['index_count']}   Columns: {s['column_count']}",
        f"Auto-increment tables: {s['auto_increment_table_count']}   "
        f"Foreign-key tables: {s['foreign_key_table_count']}   "
        f"Non-InnoDB tables: {s['non_innodb_table_count']}",
        f"Stored procedures: {s['stored_procedure_count']}   Triggers: {s['trigger_count']}   "
        f"Events: {s['event_count']}",
        f"Views: {s['view_count']} ({s['updatable_view_count']} updatable)   "
        f"Objects with a foreign DEFINER: {s['definer_object_count']}",
        "",
        "-- Cloud SQL platform --",
        f"Version: {p['mysql_version']} ({p['version_comment'] or 'unknown build'})   "
        f"Cloud SQL detected: {'yes' if p['is_cloudsql'] else 'no'}",
        f"Edition: {p['edition'] or 'unknown (not exposed over the MySQL protocol)'}",
        f"IAM database auth: {'ENABLED' if p['iam_authentication_enabled'] else 'not detected'}"
        f" ({p['iam_user_count']} IAM user(s))",
        f"Cloud SQL system users: {len(p['cloudsql_system_users'])}   "
        f"mysql.heartbeat present: {'yes' if p['has_heartbeat_table'] else 'no'}",
        f"lower_case_table_names: {p['lower_case_table_names']} (TiDB Cloud requires 2)",
        _render_topology_line(p),
    ]

    if report["continue_replication_planned"]:
        lines.append(f"Tables without a valid index: {s['tables_without_valid_index_count']}")

    lines += [
        "",
        "-- Binlog / continue-replication readiness --",
        f"continue_replication_ready: {report['binlog_precheck']['continue_replication_ready']}",
    ]
    for c in report["binlog_precheck"]["checks"]:
        marker = _STATUS_MARKER[c["status"]]
        lines.append(f"  {marker} {c['variable']}: {c['actual']} (required: {c['required']})")
        # Remediation is Cloud SQL-specific and differs per variable, so it is
        # worth the extra line whenever the check did not pass.
        if c.get("remediation"):
            lines.append(f"      fix: {c['remediation']}")

    blockers = report["assessment"]["blockers"]
    lines += ["", f"-- Blockers ({len(blockers)}) --"]
    if blockers:
        for f in blockers:
            lines.append(f"  🔴 {f['rule_id']}: {f['feature']} (count={f['count']})")
            lines.append(f"     -> {f['action']}")
    else:
        lines.append("  (none)")

    warnings = report["assessment"]["warnings"]
    lines += ["", f"-- Warnings ({len(warnings)}) --"]
    if warnings:
        for f in warnings:
            lines.append(f"  🟠 {f['rule_id']}: {f['feature']} (count={f['count']})")
            lines.append(f"     -> {f['action']}")
    else:
        lines.append("  (none)")

    score = report["score"]
    lines += ["", f"-- Readiness Score: {score['overall']}/100 ({score['rating']}) --"]
    for cat in score["categories"]:
        lines.append(f"  {cat['name']:<32s} {cat['score']:>3d}/{cat['max_points']}")
        for d in cat["deductions"]:
            lines.append(f"      {d}")

    return "\n".join(lines) + "\n"


def render_markdown(report: dict) -> str:
    s = report["summary"]
    p = report["platform"]
    score = report["score"]
    lines = [
        "# Cloud SQL for MySQL Scan Report",
        "",
        f"- Schema: `{report['schema']}`",
        f"- Target tier: {report['tier']} · Continue replication planned: "
        f"{report['continue_replication_planned']}",
        f"- MySQL version: {p['mysql_version']} ({p['version_comment'] or 'unknown build'})",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Tables | {s['table_count']} |",
        f"| Total size | {_human_bytes(s['total_size_bytes'])} |",
        f"| Indexes | {s['index_count']} |",
        f"| Columns | {s['column_count']} |",
        f"| Auto-increment tables | {s['auto_increment_table_count']} |",
        f"| Foreign-key tables | {s['foreign_key_table_count']} |",
        f"| Non-InnoDB tables | {s['non_innodb_table_count']} |",
        f"| Stored procedures / Triggers / Events | "
        f"{s['stored_procedure_count']} / {s['trigger_count']} / {s['event_count']} |",
        f"| Views (updatable) | {s['view_count']} ({s['updatable_view_count']}) |",
        f"| Objects with a foreign DEFINER | {s['definer_object_count']} |",
    ]
    if report["continue_replication_planned"]:
        lines.append(f"| Tables without a valid index | {s['tables_without_valid_index_count']} |")

    lines += [
        "",
        "## Cloud SQL platform",
        "",
        "| Property | Value |",
        "|---|---|",
        f"| Cloud SQL detected | {'yes' if p['is_cloudsql'] else 'no'} |",
        f"| Edition | {p['edition'] or 'unknown — not exposed over the MySQL protocol'} |",
        f"| IAM database authentication | "
        f"{'ENABLED' if p['iam_authentication_enabled'] else 'not detected'} "
        f"({p['iam_user_count']} user(s)) |",
        f"| Cloud SQL system users | {len(p['cloudsql_system_users'])} |",
        f"| `mysql.heartbeat` present | {'yes' if p['has_heartbeat_table'] else 'no'} |",
        f"| `sql_mode` | `{p['sql_mode'] or 'unknown'}` |",
        f"| `lower_case_table_names` | {p['lower_case_table_names']} (TiDB Cloud requires 2) |",
        f"| `gtid_mode` | {p['gtid_mode'] or 'unknown'} |",
        "",
        _render_topology_line(p),
    ]

    lines += [
        "",
        "## Binlog / continue-replication readiness",
        "",
        f"`continue_replication_ready = {report['binlog_precheck']['continue_replication_ready']}`",
        "",
        "| Variable | Status | Actual | Required | Rule | Cloud SQL remediation |",
        "|---|---|---|---|---|---|",
    ]
    for c in report["binlog_precheck"]["checks"]:
        remediation = (c.get("remediation") or "-").replace("\n", " ")
        lines.append(
            f"| {c['variable']} | {c['status']} | {c['actual']} | {c['required']} | "
            f"{c['rule_id'] or '-'} | {remediation} |"
        )

    lines += ["", "## Blockers", ""]
    if report["assessment"]["blockers"]:
        lines += ["| Rule | Feature | Count | Action |", "|---|---|---|---|"]
        for f in report["assessment"]["blockers"]:
            lines.append(f"| {f['rule_id']} | {f['feature']} | {f['count']} | {f['action']} |")
    else:
        lines.append("None detected.")

    lines += ["", "## Warnings", ""]
    if report["assessment"]["warnings"]:
        lines += ["| Rule | Feature | Count | Action |", "|---|---|---|---|"]
        for f in report["assessment"]["warnings"]:
            lines.append(f"| {f['rule_id']} | {f['feature']} | {f['count']} | {f['action']} |")
    else:
        lines.append("None detected.")

    lines += ["", f"## Readiness Score: {score['overall']}/100 ({score['rating']})", ""]
    lines += ["| Category | Score | Deductions |", "|---|---|---|"]
    for cat in score["categories"]:
        deductions = "<br>".join(cat["deductions"]) if cat["deductions"] else "-"
        lines.append(f"| {cat['name']} | {cat['score']}/{cat['max_points']} | {deductions} |")

    lines += [
        "",
        "## Not detected vs. cleared",
        "",
        "BLOCKER-5/6/7 and WARNING-5/6/7 need query-log analysis this toolkit does not "
        "perform. They are reported as **not detected**, which is not the same as cleared — "
        "confirm them against the application before treating them as passed.",
    ]

    return "\n".join(lines) + "\n"


def write_reports(report: dict, output_dir: Path, formats: tuple[str, ...]) -> dict[str, Path]:
    """Write the requested report formats; returns {format: path} for files written."""
    written: dict[str, Path] = {}
    if "json" in formats:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "tishift-cloudsql-report.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        written["json"] = path
    if "md" in formats or "markdown" in formats:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "tishift-cloudsql-report.md"
        path.write_text(render_markdown(report))
        written["md"] = path
    return written
