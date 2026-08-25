"""DDL cleanup engine — comment-preserving rewriting of Cloud SQL MySQL DDL.

Nothing is deleted. Removed clauses become plain MySQL comments tagged
TISHIFT-REMOVED, and rewritten clauses keep the original next to the
replacement, so the output stays auditable and diffs cleanly against the source
dump. Only plain `/* */` and `--` comments are emitted — never `/*! */` or
`/*T! */` executable comments. If a clause itself contains `*/`, the engine
degrades to a `--` line comment so the wrapping comment cannot close early.

Idempotent by construction: rule patterns run against a masked copy of the SQL
where string literals and comments are blanked out, so text inside an existing
TISHIFT-REMOVED comment can never re-match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tishift_cloudsql.models import CleanupFinding
from tishift_cloudsql.rules.ddl_cleanup import (
    AUTO_INCREMENT_RULE,
    CLAUSE_RULES,
    FULLTEXT_RULE,
    SPATIAL_TYPE_PATTERN,
)

REMOVED_TAG = "TISHIFT-REMOVED"
REVIEW_TAG = "TISHIFT-REVIEW"
INFO_TAG = "TISHIFT-INFO"

_TABLE_NAME = r"(?:`[^`]+`|\w+)(?:\s*\.\s*(?:`[^`]+`|\w+))?"
_CREATE_TABLE_RE = re.compile(
    rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>{_TABLE_NAME})", re.I
)
_ALTER_TABLE_RE = re.compile(rf"ALTER\s+TABLE\s+(?P<name>{_TABLE_NAME})", re.I)
_PRIMARY_KEY_RE = re.compile(r"\bPRIMARY\s+KEY\s*\((?P<cols>[^)]*)\)", re.I)


@dataclass
class StatementFlags:
    """Per-statement detections the transformer acts on after cleaning."""

    has_fulltext: bool = False
    has_spatial: bool = False
    has_auto_increment_pk: bool = False


def mask_sql(sql: str) -> str:
    """Return a same-length copy with string literals and comments blanked out.

    Quote characters and comment delimiters are preserved at their positions;
    only the interior content is replaced with 'x'. Because lengths match,
    match spans on the masked text map 1:1 onto the original.
    """
    out = list(sql)
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c in ("'", '"', "`"):
            quote = c
            j = i + 1
            while j < n:
                if sql[j] == "\\" and quote != "`" and j + 1 < n:
                    j += 2
                    continue
                if sql[j] == quote:
                    if j + 1 < n and sql[j + 1] == quote:  # doubled-quote escape
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                out[k] = "x"
            i = j + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for k in range(i, end):
                out[k] = "x"
            i = end
        elif c == "#" or (sql.startswith("--", i) and (i + 2 >= n or sql[i + 2] in " \t\r\n")):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            for k in range(i, end):
                out[k] = "x"
            i = end
        else:
            i += 1
    return "".join(out)


def split_statements(sql: str) -> list[str]:
    """Split a script on top-level ';', preserving all text (comments,
    whitespace) attached to the following chunk."""
    masked = mask_sql(sql)
    statements = []
    start = 0
    for i, ch in enumerate(masked):
        if ch == ";":
            statements.append(sql[start : i + 1])
            start = i + 1
    remainder = sql[start:]
    if remainder.strip():
        statements.append(remainder)
    elif remainder and statements:
        # Whitespace after the final semicolon is not a statement, but dropping
        # it would silently reflow the end of every converted file. Keep it
        # attached so join(split(sql)) == sql for any input.
        statements[-1] += remainder
    return statements


def normalize_table_name(raw: str) -> str:
    return re.sub(r"[`\s]", "", raw).lower()


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _comment_out(rule_id: str, clause: str) -> str:
    clause = _collapse(clause)
    if "*/" in clause:
        # A block comment would close early — degrade to a line comment.
        return f"\n-- {REMOVED_TAG} [{rule_id}]: {clause}\n"
    return f" /* {REMOVED_TAG} [{rule_id}]: {clause} */"


def _rewrite(rule_id: str, new_text: str, original: str) -> str:
    """Replace a clause, keeping the original visible in a trailing comment."""
    original = _collapse(original)
    if "*/" in original:
        return f"{new_text}\n-- {REMOVED_TAG} [{rule_id}]: was {original}\n"
    return f"{new_text} /* {REMOVED_TAG} [{rule_id}]: was {original} */"


def _split_cols(raw: str) -> list[str]:
    return [c.split("(")[0].replace("`", "").strip() for c in raw.split(",") if c.strip()]


def _pk_cols(stmt: str, masked: str) -> list[str]:
    m = _PRIMARY_KEY_RE.search(masked)
    if not m:
        return []
    return _split_cols(stmt[m.start("cols") : m.end("cols")])


def _statement_table(stmt: str, masked: str) -> str | None:
    """Extract the raw (original quoting preserved) table name, if any."""
    for pattern in (_CREATE_TABLE_RE, _ALTER_TABLE_RE):
        m = pattern.search(masked)
        if m:
            return stmt[m.start("name") : m.end("name")].strip()
    return None


def is_create_table(stmt: str) -> bool:
    return bool(_CREATE_TABLE_RE.search(mask_sql(stmt)))


def is_create_table_present(sql: str) -> bool:
    """True when a script contains at least one real CREATE TABLE.

    Masked, so a `CREATE TABLE` mentioned only inside a comment or a string
    literal does not count as a schema.
    """
    return bool(_CREATE_TABLE_RE.search(mask_sql(sql)))


def _is_table_statement(masked: str) -> bool:
    return bool(_CREATE_TABLE_RE.search(masked) or _ALTER_TABLE_RE.search(masked))


def clean_statement(stmt: str) -> tuple[str, list[CleanupFinding], StatementFlags, str | None]:
    """Apply all cleanup rules to one statement.

    Returns (new_statement, findings, flags, raw_table_name).
    """
    findings: list[CleanupFinding] = []
    flags = StatementFlags()
    masked = mask_sql(stmt)
    table_raw = _statement_table(stmt, masked)
    table = normalize_table_name(table_raw) if table_raw else None
    is_table_stmt = _is_table_statement(masked)

    # Clause-level rules. "table" rules only apply inside CREATE/ALTER TABLE;
    # "any" rules (DEFINER) also apply to views, routines, and triggers.
    matches: list[tuple[int, int, object]] = []
    for rule in CLAUSE_RULES:
        if rule.scope == "table" and not is_table_stmt:
            continue
        for m in rule.pattern.finditer(masked):
            matches.append((m.start(), m.end(), rule))
    matches.sort(key=lambda t: t[0])

    parts: list[str] = []
    prev = 0
    last_end = 0
    # Review notes are attached once per rule per statement — a table with four
    # utf8 clauses needs the caveat once, not four times.
    reviewed: set[str] = set()
    for start, end, rule in matches:
        if start < last_end:  # overlapping match from another rule — skip
            continue
        orig = stmt[start:end]
        clause = re.sub(r"^\s*,\s*", "", orig)
        suggestion = None

        if rule.replacement is not None:
            replacement = _rewrite(rule.rule_id, rule.replacement(clause), clause)
        else:
            replacement = _comment_out(rule.rule_id, clause)

        if rule.rule_id == "CSQL-DDL-6":
            flags.has_spatial = True
            suggestion = (
                "no TiDB equivalent - convert the underlying spatial column(s) to JSON "
                "and move distance or containment logic into the application (BLOCKER-4)"
            )
        elif rule.rule_id == "CSQL-DDL-4":
            suggestion = (
                "utf8mb4 widens the column - sort order changes and index key bytes grow "
                "from 3 to 4 per character; re-verify any collation-dependent uniqueness"
            )
        if suggestion is not None and rule.rule_id not in reviewed:
            reviewed.add(rule.rule_id)
            replacement += f"\n  /* {REVIEW_TAG} [{rule.rule_id}]: {suggestion} */"

        parts.append(stmt[prev:start])
        parts.append(replacement)
        findings.append(
            CleanupFinding(
                rule_id=rule.rule_id,
                risk=rule.risk,
                table=table,
                matched_text=_collapse(clause),
                action_taken=rule.action_taken,
                suggestion=suggestion,
            )
        )
        prev = end
        last_end = end
    parts.append(stmt[prev:])
    new_stmt = "".join(parts)

    # Detection-only rules run on the *cleaned* statement's mask, so a clause
    # this pass just commented out cannot also be counted as a detection.
    new_masked = mask_sql(new_stmt)

    # CSQL-DDL-5 is flagged but not reported here: whether the finding reads
    # "replica emitted" or "noted only" depends on --tiflash-replicas, which
    # only the transformer knows. It emits the finding.
    if is_table_stmt and FULLTEXT_RULE.pattern.search(new_masked):
        flags.has_fulltext = True

    if is_table_stmt and SPATIAL_TYPE_PATTERN.search(new_masked):
        flags.has_spatial = True

    # CSQL-DDL-7 fires once per table, and only for a single-column primary key —
    # AUTO_RANDOM cannot replace a composite key's leading column.
    if is_table_stmt and AUTO_INCREMENT_RULE.pattern.search(new_masked):
        pk = _pk_cols(new_stmt, new_masked)
        if len(pk) == 1:
            flags.has_auto_increment_pk = True
            findings.append(
                CleanupFinding(
                    rule_id=AUTO_INCREMENT_RULE.rule_id,
                    risk=AUTO_INCREMENT_RULE.risk,
                    table=table,
                    matched_text=f"AUTO_INCREMENT PRIMARY KEY ({pk[0]})",
                    action_taken=AUTO_INCREMENT_RULE.action_taken,
                    suggestion=(
                        f"consider AUTO_RANDOM on `{pk[0]}` to spread writes across Regions; "
                        "this changes the ID values the application sees, so it is never "
                        "applied automatically"
                    ),
                )
            )

    return new_stmt, findings, flags, table_raw
