"""Convert phase — rewrite Cloud SQL for MySQL DDL into TiDB-compatible DDL.

Scope is schema DDL only: table options, engines, charsets/collations, DEFINER
clauses, and index types (CSQL-DDL-1..7, see references/compatibility-rules.md).
Stored procedures, triggers, and events are *reported* by the scan phase as
blockers, not translated — porting application logic is a rewrite, not a
mechanical conversion, and pretending otherwise produces code nobody trusts.

Nothing is ever deleted. Every rewrite leaves the original text behind in a
`TISHIFT-REMOVED` / `TISHIFT-REVIEW` / `TISHIFT-INFO` comment, so the output
diffs cleanly against the source dump and a reviewer can always see what the
tool touched.
"""
