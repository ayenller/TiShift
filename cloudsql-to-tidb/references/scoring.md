# Readiness scoring

0–100. Mirrored by `tishift_cloudsql/rules/scoring.py` (constants) and
`core/scan/analyzers/scoring.py` (formulas).

The deduction counts come from calling the *same* `rule.check()` callables the
compatibility analyzer uses. The two can therefore never disagree about what
they counted — if a blocker appears in the findings table, its deduction is in
the score, and vice versa.

## Categories

| Category | Max | What it measures |
|---|---|---|
| Schema compatibility | 30 | Types, charsets, indexes, name collisions |
| Programmable objects | 25 | Stored procedures, triggers, events, UDFs |
| Cloud SQL platform surface | 20 | IAM auth, DEFINERs, engine mix, 5.7, system artifacts, topology |
| Data & load feasibility | 15 | Size vs. tier capacity, export path, network path |
| Cutover & continue replication | 10 | Binlog config, DM reachability, valid indexes |

## Deductions

| Category | Deduction | Points |
|---|---|---|
| Schema compatibility | spatial columns (BLOCKER-4) | 5 per table |
| | unsupported charset (BLOCKER-8) | 5 per table |
| | table-name case collision (BLOCKER-9) | 5 per colliding group |
| | FULLTEXT index without a real index (WARNING-2) | 2 per index |
| | `lower_case_table_names` mismatch (WARNING-8) | 2 flat |
| | updatable view (WARNING-9) | 1 per view |
| Programmable objects | stored procedures (BLOCKER-1) | 5 per batch of 10 |
| | triggers (BLOCKER-2) | 5 per batch of 10 |
| | events (BLOCKER-3) | 3 per event |
| | UDFs present (BLOCKER-6) | 5 flat |
| | XA present (BLOCKER-5) | 3 flat |
| Cloud SQL platform | IAM database auth in use (CSQL-WARNING-1) | 4 flat |
| | DEFINER on a Cloud SQL/IAM principal (CSQL-WARNING-2) | 1 per object, max 5 |
| | MySQL 5.7 source (CSQL-WARNING-3) | 3 flat |
| | `sql_mode` mismatch (CSQL-WARNING-4) | 2 flat |
| | Cloud SQL system artifacts in scope (CSQL-WARNING-5) | 2 flat |
| | downstream read replicas (CSQL-WARNING-6) | 2 flat |
| | non-InnoDB tables (CSQL-WARNING-7) | 3 per table, max 6 |
| Data & load feasibility | source larger than the tier's capacity | 5 flat |
| | no confirmed network path | 5 flat |
| | no GCS export bucket configured | 2 flat |
| Cutover & replication | source is a read replica (CSQL-BLOCKER-1) | 10 flat |
| | continue replication wanted but tier is Starter | 5 flat |
| | binary logging off (CSQL-WARNING-8) | 5 flat |
| | `binlog_format` / `binlog_row_image` wrong (CSQL-WARNING-9/10) | 3 flat |
| | binlog retention too short (CSQL-WARNING-11) | 2 flat |
| | `binlog_row_value_options` set (CSQL-WARNING-12) | 2 flat |
| | `binlog_transaction_compression` on (CSQL-WARNING-13) | 2 flat |
| | table without a valid index | 2 per table |

Every category floors at 0 — a schema with 40 stored procedures loses the whole
25-point category, not more.

Continue-replication deductions apply **only** when continue replication is
planned. A cutover-only migration is not penalised for a source with binary
logging switched off.

## Rating bands

| Score | Rating |
|---|---|
| 85–100 | READY |
| 65–84 | READY WITH WORK |
| 40–64 | SIGNIFICANT REWORK |
| 0–39 | NOT RECOMMENDED YET |

Bands are identical across every TiShift module so scores are comparable.

## Output format

Report the overall score, the rating, and a per-category breakdown showing
`score / max` plus the individual deductions with their rule IDs. Never report a
bare number — the breakdown is the actionable part.
