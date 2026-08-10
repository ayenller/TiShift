"""Convert-phase orchestration: DDL cleanup → validation → TiFlash emission.

Pipeline per statement:

1. `ddl_cleaner` rewrites or comments out Cloud SQL-specific syntax, tagging
   everything TISHIFT-REMOVED / TISHIFT-REVIEW.
2. Modified statements are re-parsed with sqlglot (MySQL dialect) to verify the
   rewrite left valid syntax. Failures are reported, never silently dropped —
   the CLI exits non-zero on any parse error.
3. Each table carrying a FULLTEXT index (CSQL-DDL-5) gets
   `ALTER TABLE ... SET TIFLASH REPLICA n` emitted immediately after its
   CREATE TABLE. Outside Starter the FULLTEXT index is parse-only, so columnar
   scans on TiFlash are what stands in for it.

TiFlash replica statements are emitted on every tier; only
`--tiflash-replicas 0` downgrades the ALTER to an informational comment.

Trade-off worth knowing: replicas created before the data load mean TiFlash
replicates during the import, which slows large loads. references/load-strategies.md
recommends moving these ALTERs to the end of the script when load time matters.
"""

from __future__ import annotations

import re

from tishift_cloudsql.core.convert.ddl_cleaner import (
    INFO_TAG,
    REVIEW_TAG,
    clean_statement,
    is_create_table,
    mask_sql,
    normalize_table_name,
    split_statements,
)
from tishift_cloudsql.models import CleanupFinding, DDLCleanupResult

_EXISTING_TIFLASH_RE = re.compile(
    r"ALTER\s+TABLE\s+(?P<name>(?:`[^`]+`|\w+)(?:\s*\.\s*(?:`[^`]+`|\w+))?)"
    r"\s+SET\s+TIFLASH\s+REPLICA\b",
    re.I,
)

# Info comments emitted on a previous run down the tiflash_replicas=0 path.
# Matched on the raw text, because mask_sql blanks comments out.
_EXISTING_INFO_NOTE_RE = re.compile(
    r"\[CSQL-DDL-5\][^\n]*?\bALTER\s+TABLE\s+"
    r"(?P<name>(?:`[^`]+`|\w+)(?:\s*\.\s*(?:`[^`]+`|\w+))?)",
    re.I,
)


def _existing_tiflash_tables(sql: str) -> set[str]:
    masked = mask_sql(sql)
    return {
        normalize_table_name(sql[m.start("name") : m.end("name")])
        for m in _EXISTING_TIFLASH_RE.finditer(masked)
    }


def _existing_info_notes(sql: str) -> set[str]:
    return {normalize_table_name(m.group("name")) for m in _EXISTING_INFO_NOTE_RE.finditer(sql)}


def _parse_error(stmt: str) -> str | None:
    """Parse a statement with sqlglot; return an error string or None.

    sqlglot logs a warning and degrades to a generic `Command` node for syntax
    its MySQL dialect does not model. That is noise here — we do our own
    reporting — so the logger is quietened for the duration of the call.
    """
    import logging

    import sqlglot

    logger = logging.getLogger("sqlglot")
    previous = logger.level
    logger.setLevel(logging.ERROR)
    try:
        sqlglot.parse(stmt, read="mysql")
        return None
    except Exception as exc:  # sqlglot raises several error types
        return str(exc).splitlines()[0]
    finally:
        logger.setLevel(previous)


def _validate_rewrite(original: str, rewritten: str) -> str | None:
    """Report only errors this conversion introduced.

    sqlglot's MySQL dialect has gaps of its own — `SRID` on a spatial column is
    one — and a statement it could never parse in the first place is not
    evidence that the rewrite broke anything. So a parse failure only counts
    when the original parsed and the rewritten form does not.
    """
    error = _parse_error(rewritten)
    if error is None:
        return None
    if _parse_error(original) is not None:
        return None
    return error


def transform_schema(
    sql: str,
    tier: str = "starter",
    tiflash_replicas: int = 2,
) -> DDLCleanupResult:
    """Clean a DDL script and inline TiFlash replica statements."""
    result = DDLCleanupResult()
    tier_normalized = (tier or "starter").strip().lower()
    # Idempotency guards: a second run over this function's own output must not
    # duplicate the ALTERs or the info comments it emitted the first time.
    already_replicated = _existing_tiflash_tables(sql)
    already_noted = _existing_info_notes(sql)

    parts: list[str] = []
    for stmt in split_statements(sql):
        new_stmt, findings, flags, table_raw = clean_statement(stmt)
        parts.append(new_stmt)
        result.findings.extend(findings)

        if new_stmt != stmt:
            error = _validate_rewrite(stmt, new_stmt)
            if error:
                result.parse_errors.append(f"{table_raw or '<statement>'}: {error}")

        if not table_raw:
            continue
        normalized = normalize_table_name(table_raw)

        if flags.has_spatial and normalized not in result.spatial_tables:
            result.spatial_tables.append(normalized)

        if not (is_create_table(stmt) and flags.has_fulltext):
            continue

        result.fulltext_tables.append(normalized)
        if normalized in already_replicated or normalized in already_noted:
            continue

        alter = f"ALTER TABLE {table_raw} SET TIFLASH REPLICA {tiflash_replicas};"
        review = (
            f"/* {REVIEW_TAG} [CSQL-DDL-5]: FULLTEXT index is parse-only on this tier - "
            f"the TiFlash replica accelerates scan-based full-text filtering (LIKE / "
            f"REGEXP) instead; rewrite MATCH ... AGAINST queries, they will not use an "
            f"index */"
        )
        if tiflash_replicas > 0:
            parts.append(f"\n\n{review}\n{alter}\n")
            result.tiflash_statements.append(alter)
            action = "tiflash_replica_emitted"
        else:
            parts.append(
                f"\n-- {INFO_TAG} [CSQL-DDL-5]: TiFlash replica not emitted "
                f"(tier={tier_normalized}, replicas={tiflash_replicas}); "
                f"to enable later run: {alter}\n"
            )
            action = "noted_only"

        result.findings.append(
            CleanupFinding(
                rule_id="CSQL-DDL-5",
                risk="assess",
                table=normalized,
                matched_text=f"FULLTEXT index on {table_raw}",
                action_taken=action,
                suggestion=(
                    "FULLTEXT indexes are parse-only outside Starter - the TiFlash replica "
                    "accelerates scan-based full-text filtering (LIKE / REGEXP) on the "
                    "columnar engine; rewrite MATCH ... AGAINST queries or move them to an "
                    "external search engine"
                ),
            )
        )

    result.sql = "".join(parts)
    return result
