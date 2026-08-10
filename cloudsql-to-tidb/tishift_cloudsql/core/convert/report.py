"""DDL cleanup report writers — JSON (machine) and Markdown (human)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from tishift_cloudsql.models import DDLCleanupResult
from tishift_cloudsql.rules.ddl_cleanup import ALL_RULES

_RISK_BADGE = {
    "blocker": "🔴 blocker",
    "info": "🔵 info",
    "assess": "🟠 needs assessment",
    "harmless": "🟢 harmless",
}
_AUTO_BADGE = {"yes": "✅ yes", "partial": "⚠️ partial", "no": "❌ not needed"}


def build_report(
    result: DDLCleanupResult,
    source_file: str,
    tier: str,
    tiflash_replicas: int,
) -> dict:
    counts = Counter(f.rule_id for f in result.findings)
    return {
        "source_file": source_file,
        "tier": tier,
        "tiflash_replicas": tiflash_replicas,
        "summary": {
            rule.rule_id: {
                "description": rule.description,
                "risk": rule.risk,
                "auto_cleanable": rule.auto_cleanable,
                "count": counts.get(rule.rule_id, 0),
            }
            for rule in ALL_RULES
        },
        "findings": [asdict(f) for f in result.findings],
        "fulltext_tables": result.fulltext_tables,
        "spatial_tables": result.spatial_tables,
        "tiflash_statements": result.tiflash_statements,
        "parse_errors": result.parse_errors,
        "notes": [
            "Nothing is deleted: removed clauses are preserved as TISHIFT-REMOVED "
            "comments, and rewritten clauses keep the original alongside the replacement.",
            "Running convert over its own output is a no-op — rules match against a mask "
            "that blanks out comments, so previously-tagged text cannot re-match.",
            "TiFlash replicas are emitted before the data load; TiFlash then replicates "
            "during the import, which slows large loads. Move the ALTERs to the end of the "
            "script if import speed matters.",
            "Apply the output wrapped in SET FOREIGN_KEY_CHECKS=0 / =1 — per-table "
            "SHOW CREATE TABLE output is not FK-topologically ordered and will otherwise "
            "fail with ERROR 1824.",
        ],
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# DDL Cleanup Report",
        "",
        f"- Source: `{report['source_file']}`",
        f"- Target tier: {report['tier']} · TiFlash replicas: {report['tiflash_replicas']}",
        "",
        "## Rule summary",
        "",
    ]
    # Rules with zero hits are omitted while any rule matched; when nothing
    # matched at all, every rule is shown (0 hits) as evidence of what was
    # checked. The JSON report always keeps the full rule set.
    hit_rules = {rid: s for rid, s in report["summary"].items() if s["count"]}
    display_rules = hit_rules or report["summary"]
    lines += ["| Rule | Syntax | Risk | Auto-cleanable | Hits |", "|---|---|---|---|---|"]
    for rule_id, s in display_rules.items():
        lines.append(
            f"| {rule_id} | {s['description']} | {_RISK_BADGE[s['risk']]} "
            f"| {_AUTO_BADGE[s['auto_cleanable']]} | {s['count']} |"
        )

    lines += ["", "## Findings", ""]
    if report["findings"]:
        lines += ["| Table | Rule | Action | Matched text |", "|---|---|---|---|"]
        for f in report["findings"]:
            lines.append(
                f"| {f['table'] or '-'} | {f['rule_id']} | {f['action_taken']} "
                f"| `{f['matched_text']}` |"
            )
    else:
        lines.append("No Cloud SQL-specific syntax detected.")

    review = [f for f in report["findings"] if f["risk"] in ("assess", "blocker")]
    lines += ["", "## Manual review (🟠 needs assessment / 🔴 blocker)", ""]
    if review:
        for f in review:
            lines.append(f"- **{f['table'] or '-'}** — `{f['matched_text']}` ({f['rule_id']})")
            if f.get("suggestion"):
                lines.append(f"  - Suggestion: {f['suggestion']}")
    else:
        lines.append("Nothing to review.")

    lines += ["", "## TiFlash replicas", ""]
    fulltext_tables = report.get("fulltext_tables", [])
    if report["tiflash_statements"]:
        lines += ["```sql", *report["tiflash_statements"], "```"]
    elif fulltext_tables:
        lines.append(
            "FULLTEXT tables detected but no replica statements emitted "
            f"(replicas={report['tiflash_replicas']}) — see TISHIFT-INFO comments in the "
            "output SQL."
        )
    else:
        lines.append("No FULLTEXT indexes detected — no TiFlash replicas needed.")
    if fulltext_tables:
        lines += [
            "",
            "FULLTEXT-index tables (CSQL-DDL-5: parse-only outside Starter — the TiFlash "
            "replica accelerates scan-based full-text filtering; rewrite MATCH ... AGAINST "
            "queries): " + ", ".join(f"`{t}`" for t in fulltext_tables),
        ]

    spatial_tables = report.get("spatial_tables", [])
    if spatial_tables:
        lines += [
            "",
            "## Spatial tables (BLOCKER-4 / CSQL-DDL-6)",
            "",
            "Spatial indexes were commented out, but the column types were **not** changed — "
            "converting them to JSON and moving the geometry logic into the application is a "
            "rewrite this tool will not attempt: "
            + ", ".join(f"`{t}`" for t in spatial_tables),
        ]

    if report["parse_errors"]:
        lines += ["", "## Parse errors (cleanup left invalid syntax — fix manually)", ""]
        lines += [f"- {e}" for e in report["parse_errors"]]

    lines += ["", "## Notes", ""]
    lines += [f"- {n}" for n in report["notes"]]
    return "\n".join(lines) + "\n"


def write_reports(
    result: DDLCleanupResult,
    output_dir: Path,
    source_file: str,
    tier: str,
    tiflash_replicas: int,
) -> tuple[Path, Path]:
    """Write JSON + Markdown reports; returns their paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(result, source_file, tier, tiflash_replicas)
    json_path = output_dir / "ddl-cleanup-report.json"
    md_path = output_dir / "ddl-cleanup-report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    md_path.write_text(render_markdown(report))
    return json_path, md_path
