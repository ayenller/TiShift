"""DDL cleanup rule registry — the convert phase's CSQL-DDL-* rules.

Single source of truth for the CSQL-DDL rules; kept in lockstep with
references/compatibility-rules.md (§ DDL cleanup rules).

Patterns are matched against a *masked* copy of the SQL (string literals and
comments blanked out, lengths preserved) so a keyword mentioned inside a string
or inside an existing TISHIFT-REMOVED comment is never matched. That is what
makes convert idempotent: running it over its own output is a no-op.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CleanupRule:
    rule_id: str
    description: str
    risk: str  # blocker | assess | info | harmless
    action_taken: str  # commented_out | rewritten | commented_out_with_suggestion | kept
    auto_cleanable: str  # yes | partial | no
    pattern: re.Pattern
    # When set, the matched span is replaced by this function's return value
    # instead of being commented out. The original text is preserved in a
    # trailing TISHIFT-REMOVED comment either way — nothing is ever lost.
    replacement: Callable[[str], str] | None = None
    # "table" rules only run inside CREATE TABLE / ALTER TABLE; "any" rules run
    # on every statement (DEFINER shows up on views, routines, and triggers).
    scope: str = "table"


# A table-option value: quoted string, backtick identifier, or bare word.
# Matched on masked text, so quoted forms appear as 'xxx' / "xxx" — the
# original value is recovered from the match span against the unmasked source.
_VALUE = r"(?:\"[^\"]*\"|'[^']*'|`[^`]*`|\w+)"

# A user specification: `user`@`host`, 'user'@'host', or bare words.
_USER = r"(?:`[^`]+`|'[^']*'|\"[^\"]*\"|[\w$]+)"


def _to_innodb(_matched: str) -> str:
    return "ENGINE=InnoDB"


# Trailing guard is (?![0-9A-Za-z]) rather than \b: `_` counts as a word
# character, so \b would refuse to match the `utf8` in `utf8_general_ci` — the
# exact case this rewrite exists for. Excluding only alphanumerics still keeps
# `utf8mb4` from matching, since `m` follows `utf8` there.
_UTF8_PREFIX = re.compile(r"\butf8(?:mb3)?(?![0-9A-Za-z])", re.I)


def _widen_utf8(matched: str) -> str:
    """Rewrite utf8/utf8mb3 to utf8mb4 in place, preserving the rest of the clause.

    Textual rather than table-driven because the collation suffix carries
    through unchanged: utf8_general_ci -> utf8mb4_general_ci,
    utf8mb3_bin -> utf8mb4_bin.
    """
    return _UTF8_PREFIX.sub("utf8mb4", matched)


# Clause-level rules. Each pattern optionally consumes a preceding comma so
# removing the clause never leaves a dangling separator.
CLAUSE_RULES: list[CleanupRule] = [
    CleanupRule(
        rule_id="CSQL-DDL-1",
        description=(
            "DEFINER clause — names a Cloud SQL or IAM principal that cannot exist on TiDB, "
            "so applying the DDL as-is fails"
        ),
        risk="info",
        action_taken="commented_out",
        auto_cleanable="yes",
        pattern=re.compile(rf"\bDEFINER\s*=\s*{_USER}(?:@{_USER})?", re.I),
        scope="any",
    ),
    CleanupRule(
        rule_id="CSQL-DDL-2",
        description=(
            "Non-InnoDB storage engine — TiDB has one engine. Note that MyISAM's "
            "non-transactional semantics may be load-bearing in the application"
        ),
        risk="assess",
        action_taken="rewritten",
        auto_cleanable="yes",
        pattern=re.compile(
            r"\bENGINE\s*=\s*(?:MyISAM|MEMORY|HEAP|ARCHIVE|CSV|BLACKHOLE|FEDERATED)\b", re.I
        ),
        replacement=_to_innodb,
    ),
    CleanupRule(
        rule_id="CSQL-DDL-3",
        description="Storage/statistics table options with no TiDB analog",
        risk="harmless",
        action_taken="commented_out",
        auto_cleanable="yes",
        pattern=re.compile(
            r"(?:,\s*)?\b(?:"
            r"(?:ROW_FORMAT|KEY_BLOCK_SIZE|ENCRYPTION|COMPRESSION|STATS_PERSISTENT"
            r"|STATS_AUTO_RECALC|STATS_SAMPLE_PAGES)\s*=\s*" + _VALUE + r""
            r"|TABLESPACE\s+" + _VALUE + r""
            r")",
            re.I,
        ),
    ),
    CleanupRule(
        rule_id="CSQL-DDL-4",
        description=(
            "utf8/utf8mb3 charset or collation — widened to utf8mb4. Sort order and the "
            "byte width of index keys both change"
        ),
        risk="assess",
        action_taken="rewritten",
        auto_cleanable="yes",
        pattern=re.compile(
            r"\b(?:(?:DEFAULT\s+)?(?:CHARACTER\s+SET|CHARSET)\s*=?\s*utf8(?:mb3)?"
            r"|COLLATE\s*=?\s*utf8(?:mb3)?_\w+)\b",
            re.I,
        ),
        replacement=_widen_utf8,
    ),
    CleanupRule(
        rule_id="CSQL-DDL-6",
        description=(
            "SPATIAL index — unsupported by TiDB, and the underlying spatial column types "
            "are a BLOCKER-4 application rewrite, not a mechanical conversion"
        ),
        risk="blocker",
        action_taken="commented_out_with_suggestion",
        auto_cleanable="partial",
        pattern=re.compile(r"(?:,\s*)?\bSPATIAL\s+(?:KEY|INDEX)\b[^,\n]*", re.I),
    ),
]

# Mapping rule: FULLTEXT indexes are parse-only outside Starter (WARNING-2) —
# TiDB accepts the syntax but builds no index, so the clause itself is harmless
# and is KEPT (removing it would make the DDL diverge from the source for no
# gain). The TiDB-side answer is a TiFlash replica on the table: columnar scans
# accelerate scan-based filtering (LIKE / REGEXP) in place of the index.
# MATCH ... AGAINST queries still need rewriting, so the emitted ALTER carries a
# TISHIFT-REVIEW note.
FULLTEXT_RULE = CleanupRule(
    rule_id="CSQL-DDL-5",
    description=(
        "FULLTEXT index (parse-only outside Starter — TiFlash replica emitted to "
        "accelerate scan-based full-text filtering; rewrite MATCH ... AGAINST)"
    ),
    risk="assess",
    action_taken="kept",
    auto_cleanable="partial",
    pattern=re.compile(r"\bFULLTEXT\s+(?:KEY|INDEX)\b", re.I),
)

# Suggestion-only rule: a single-column integer PK with AUTO_INCREMENT
# concentrates every insert on one Region. AUTO_RANDOM spreads them, but it
# changes the ID values the application observes, so this is NEVER applied —
# it only attaches a TISHIFT-REVIEW note to the table.
AUTO_INCREMENT_RULE = CleanupRule(
    rule_id="CSQL-DDL-7",
    description=(
        "AUTO_INCREMENT primary key — sequential IDs concentrate writes on one Region; "
        "consider AUTO_RANDOM (suggestion only, never applied automatically)"
    ),
    risk="info",
    action_taken="kept",
    auto_cleanable="no",
    pattern=re.compile(r"\bAUTO_INCREMENT\b(?!\s*=)", re.I),
)

# Detection-only: spatial column types mark the table for review even when it
# carries no SPATIAL index. Not a CleanupRule because nothing is rewritten —
# dropping or retyping the column is BLOCKER-4, a human decision.
SPATIAL_TYPE_PATTERN = re.compile(
    r"\b(?:GEOMETRYCOLLECTION|MULTILINESTRING|MULTIPOLYGON|GEOMETRY|LINESTRING"
    r"|MULTIPOINT|POLYGON|POINT)\b",
    re.I,
)

# Canonical rule list for report rendering.
ALL_RULES: list[CleanupRule] = [*CLAUSE_RULES, FULLTEXT_RULE, AUTO_INCREMENT_RULE]
