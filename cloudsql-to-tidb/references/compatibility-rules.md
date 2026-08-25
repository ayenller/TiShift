# Cloud SQL for MySQL → TiDB compatibility rules

Canonical rule corpus. `tishift_cloudsql/rules/compatibility.py`,
`rules/binlog_check.py`, and `rules/ddl_cleanup.py` mirror this file 1:1 — change
one, change the other. IDs are stable; renumbering breaks every report ever
generated.

Two namespaces:

- **`BLOCKER-N` / `WARNING-N`** — generic MySQL → TiDB rules, shared vocabulary
  with every other TiShift module. Numbering matches the other modules on
  purpose (including the absent `WARNING-1`) so scores are comparable.
- **`CSQL-*`** — the Cloud SQL managed-platform layer. This is what makes the
  module more than "Aurora with a different hostname".

A note that applies to every remediation below: **`SET GLOBAL` does not work on
Cloud SQL.** No user holds `SUPER` — not even one granted `cloudsqlsuperuser` —
so every server variable change is `gcloud sql instances patch`, an instance
setting, or nothing at all. Rules that say "set X" without a `gcloud` command
are rules where the variable is *not* user-configurable.

---

## Blockers

| ID | Condition | Feature | Action |
|---|---|---|---|
| BLOCKER-1 | `routines` contains a PROCEDURE | Stored procedures — parsed but cannot execute | Convert to application code (Python/Go/Java/JS) |
| BLOCKER-2 | any trigger | Triggers — parsed but cannot execute | Move logic to application middleware |
| BLOCKER-3 | any event | Scheduled events — not supported | Use cron, Kubernetes CronJob, or Cloud Scheduler + Cloud Run |
| BLOCKER-4 | column type in the spatial set | Spatial/GIS columns — type, functions, and indexes all unsupported | Convert columns to JSON with `COMMENT 'was: <original_type>'` |
| BLOCKER-5 | XA seen in query log | XA distributed transactions — not supported | Refactor to single-shard transactions or a saga |
| BLOCKER-6 | UDFs seen in query log | User-defined functions — not supported | Convert to application-layer functions |
| BLOCKER-7 | `ExtractValue`/`UpdateXML` in query log | XML functions — not supported | Process XML in the application layer |
| BLOCKER-8 | column charset ∉ {ascii, latin1, binary, utf8, utf8mb3, utf8mb4, gbk} | Unsupported character set | Convert affected columns to utf8mb4 before export. `utf8mb3` is in the accepted set because that is how MySQL 8.0.30+ reports a `utf8` column in `information_schema`; it is widened to utf8mb4 by CSQL-DDL-4 rather than blocked |
| BLOCKER-9 | table names differing only by case, and `lower_case_table_names != 2` | TiDB Cloud only supports `lower_case_table_names=2` (case-insensitive) | Rename one of each colliding pair before migrating. **On Cloud SQL you cannot fix this by changing the source**: MySQL 8.0 forbids changing `lower_case_table_names` after initialization and it is not an exposed Cloud SQL flag, so the rename is the only path |
| CSQL-BLOCKER-1 | source instance is a Cloud SQL **read replica** *and* continue replication is planned | Google requires that the source of an external replica "must be a primary or standalone instance" | Point DM at the primary instead, or fall back to a cutover-only migration |

`BLOCKER-5`, `BLOCKER-6`, and `BLOCKER-7` need query-log analysis this toolkit
does not perform. They are still registered and default to **not detected** —
reports must never render them as "clear".

## Warnings

| ID | Condition | Feature | Action |
|---|---|---|---|
| WARNING-2 | FULLTEXT index, non-Starter tier | Real FULLTEXT index support is Starter-only | Add a TiFlash replica so columnar scans accelerate `LIKE`/`REGEXP` filtering (convert emits this, CSQL-DDL-5); rewrite `MATCH … AGAINST`; or use a dedicated search engine |
| WARNING-3 | any `AUTO_INCREMENT` table | AUTO_INCREMENT is unique but NOT sequential | Consider `AUTO_RANDOM` for high-insert tables (CSQL-DDL-7 suggests this); if truly sequential IDs are needed, TiDB's MySQL Compatibility Mode costs throughput |
| WARNING-4 | `utf8mb4_0900_*` collation | MySQL 8 default collations | Maps 1:1 on TiDB (supported since v7.4) — no action needed |
| WARNING-5 | `GET_LOCK` in query log | Limited advisory-lock implementation | Test behaviour; consider Redis-based locks |
| WARNING-6 | `SQL_CALC_FOUND_ROWS` in query log | Works, but forces a full table scan | Replace with a separate `COUNT(*)` |
| WARNING-7 | `SAVEPOINT` in query log | Pessimistic mode only | Ensure pessimistic transactions (TiDB default) |
| WARNING-8 | `lower_case_table_names != 2` | TiDB always compares table names case-insensitively | Verify no application code depends on case-sensitive matching. Not fixable on the Cloud SQL side — see BLOCKER-9 |
| WARNING-9 | `IS_UPDATABLE = YES` view | TiDB views are always read-only | Redirect writes through the view to the base tables |

## Cloud SQL platform warnings

| ID | Condition | Feature | Action |
|---|---|---|---|
| CSQL-WARNING-1 | `cloudsql_iam_authentication = on`, or users authenticating with the IAM plugin | IAM database authentication has no TiDB equivalent | Re-provision those principals as password users on TiDB and update every client's credential source before cutover |
| CSQL-WARNING-2 | a view/routine/trigger whose `DEFINER` names a `cloudsql*` or IAM principal | The definer cannot exist on TiDB, so applying the DDL fails | Strip the `DEFINER` clause — the convert phase does this automatically (CSQL-DDL-1) |
| CSQL-WARNING-3 | `@@version` starts with `5.7` | TiDB targets MySQL 8.0 semantics | Review 5.7-only behaviour: default `utf8mb3`, lax zero-date handling, `ZEROFILL` display width, and pre-8.0 collation defaults |
| CSQL-WARNING-4 | source `sql_mode` differs from the TiDB target's | Behavioural drift that surfaces as data errors after cutover, not at migration time | Compare the two explicitly and align the application's expectations. `sql_mode` may or may not be patchable on your instance — check `gcloud sql instances describe` before assuming |
| CSQL-WARNING-5 | `mysql.heartbeat` present, or `cloudsql*` system users in scope | Cloud SQL's own bookkeeping objects will otherwise be dumped into TiDB and replicated by DM | Exclude the `mysql` schema from the dump and from DM's block-allow-list; never migrate the `cloudsql*` users |
| CSQL-WARNING-6 | downstream read replicas attached | Replicas keep serving stale reads after cutover | Decide replica fate explicitly in the cutover plan; a replica left running is a split-brain read path |
| CSQL-WARNING-7 | any table whose engine is not InnoDB | Cloud SQL permits MyISAM; TiDB has one engine | Convert to InnoDB before export — the convert phase rewrites the clause (CSQL-DDL-2), but MyISAM's non-transactional semantics may be load-bearing in the application |
| CSQL-WARNING-14 | a foreign key whose referenced columns are not covered by a PRIMARY or UNIQUE key on the parent | **Not a TiDB problem** — TiDB accepts and enforces these. It is a *portability* problem: MySQL 8.0.16+ dropped the old InnoDB extension and rejects them with ERROR 6125 | Add a UNIQUE key covering the referenced columns on the parent if you need the dump to stay re-appliable to MySQL — a rollback target, a staging refresh, or a new Cloud SQL instance all need that. Scored at **0 penalty** |

`CSQL-WARNING-14` is not strictly Cloud SQL–specific — it is a general MySQL
8.0.16+ restriction — but it lives in this namespace rather than claiming a
shared `BLOCKER-N`/`WARNING-N` id that other TiShift modules have not agreed on.

The trap is that a *prefix* of a unique key is not unique. A parent with
`PRIMARY KEY (branch_code, debtor_no)` does not make `branch_code` unique, so a
child FK referencing `branch_code` alone is not backed by a unique constraint —
even though the parent has a (non-unique) index on exactly that column.

Verified on both ends against a real 250-table ERP schema:

| Target | Result |
|---|---|
| Cloud SQL for MySQL 8.4.10 | **ERROR 6125**, apply stops at table 57 of 250 — for the original dump *and* the converted one |
| TiDB v8.5.3 | All 250 tables created. FK enforced: ERROR 1452 on an orphan child row, ERROR 1451 on a restricted parent delete |

## Continue-replication (binlog) prechecks

These gate **Phase 7 only**. A cutover-only migration does not need any of them,
and the scoring engine does not deduct for them unless continue replication is
planned.

| ID | Variable / setting | Required | Cloud SQL remediation |
|---|---|---|---|
| CSQL-WARNING-8 | `log_bin` | `ON` | **Not a database flag, and the Console calls it Point-in-time recovery** (Edit → Data Protection → Enable point-in-time recovery). CLI: `--enable-bin-log` — Google's docs say explicitly *not* `--enable-point-in-time-recovery`, which is the PostgreSQL flag. Requires automatic backups already enabled, and **restarts the instance** |
| CSQL-WARNING-9 | `binlog_format` | `ROW` | **Not user-configurable.** Cloud SQL sets `binlog_format=ROW` itself when binary logging is on. A non-ROW value means binary logging is off or the instance is not what it appears to be — investigate rather than patch |
| CSQL-WARNING-10 | `binlog_row_image` | `FULL` | `gcloud sql instances patch <INSTANCE> --database-flags=binlog_row_image=full` (configurable, no restart) |
| CSQL-WARNING-11 | continue replication is planned | **Retention cannot be read over the MySQL protocol.** `binlog_expire_logs_seconds` is NOT the authoritative value — the instance's `transactionLogRetentionDays` is, and it does not track the variable (observed: instance raised 1 → 7 while the variable stayed at 86400, and it was absent from `databaseFlags`) | Always reported when replicating, scored at **0**. Verify yourself: `gcloud sql instances describe <INSTANCE> --format='value(settings.backupConfiguration.transactionLogRetentionDays)'`. Max 7 on Enterprise, 35 on Enterprise Plus |
| CSQL-WARNING-12 | `binlog_row_value_options` | empty, **not** `PARTIAL_JSON` | Clear the flag. There is no "unset one flag" verb — `--database-flags` *replaces* the whole list, so re-issue it without `binlog_row_value_options`, or use `--clear-database-flags` if it is the only flag set. Partial-JSON binlog rows cause **silent corruption** of JSON columns under DM, not a clean failure |
| CSQL-WARNING-13 | `binlog_transaction_compression` | `OFF` | **Not an exposed Cloud SQL flag**, and off by default. If it somehow reads `ON`, open a support case rather than expecting a patch to work |

Informational, collected but not gated:

- `server_id` — must be non-zero; `0` disables binary logging silently.
- `gtid_mode` / `enforce_gtid_consistency` — Cloud SQL enforces GTID and it
  cannot be turned off. This is *good* for DM (position-independent resume),
  but it also means `enforce_gtid_consistency` statement restrictions already
  apply to the source, so nothing new breaks at migration time.

### Network reachability — the trap specific to Cloud SQL

The Cloud SQL Auth Proxy is a **local, client-side** connector. It authenticates
with your IAM credentials and exposes a loopback socket.

| Path | `scan` / `convert` / Dumpling | TiDB Cloud DM |
|---|---|---|
| Cloud SQL Auth Proxy | ✅ recommended | ❌ **cannot be used** — DM is a managed service and has no host to run the proxy on |
| Public IP + authorized networks | ✅ | ✅ — allowlist TiDB Cloud's DM egress addresses |
| Private IP + VPC peering / Private Service Connect | ✅ | ✅ — requires matching network configuration on the TiDB Cloud side |

A migration that scans cleanly through the proxy and only discovers this at
Phase 7 has to redo its network design under time pressure. Decide the path in
Phase 1.

The replication user Google documents for external replicas:

```sql
CREATE USER 'REPLICATION_USER'@'%' IDENTIFIED BY 'REPLICATION_USER_PASSWORD';
GRANT REPLICATION SLAVE ON *.* TO 'REPLICATION_USER'@'%';
```

DM additionally wants `REPLICATION CLIENT` and `SELECT` on the migrated schemas.
`RELOAD` may not be grantable on a managed instance — if DM's precheck demands
it, configure the task to rely on GTID rather than a consistent snapshot lock.

---

## DDL cleanup rules (convert phase)

Applied by `core/convert/`. Every rule is matched against a length-preserving
mask of the SQL, so text already inside a `TISHIFT-*` comment can never
re-match and running convert twice is a no-op.

| ID | Trigger | Risk | Action |
|---|---|---|---|
| CSQL-DDL-1 | `DEFINER=<user>@<host>` | info | Commented out — the Cloud SQL principal will not exist on TiDB |
| CSQL-DDL-2 | `ENGINE=MyISAM\|MEMORY\|ARCHIVE\|CSV` | assess | Rewritten to `ENGINE=InnoDB`, original preserved in the comment |
| CSQL-DDL-3 | `ROW_FORMAT=COMPRESSED`, `KEY_BLOCK_SIZE`, `ENCRYPTION`, `COMPRESSION`, `TABLESPACE`, `STATS_PERSISTENT`, `STATS_AUTO_RECALC`, `STATS_SAMPLE_PAGES` | harmless | Commented out — no TiDB analog, no data impact |
| CSQL-DDL-4 | `CHARSET=utf8`/`utf8mb3` and `utf8mb3_*` collations | assess | Rewritten to `utf8mb4` / `utf8mb4_general_ci`, with a `TISHIFT-REVIEW` note that sort order and max column width change |
| CSQL-DDL-5 | `FULLTEXT KEY …` | assess | Clause **kept** — TiDB parses it and builds no index, so it is inert. A TiFlash replica is emitted for the table so scan-based filtering stays fast |
| CSQL-DDL-6 | `SPATIAL KEY …` | blocker | Commented out with a `TISHIFT-REVIEW`. Spatial *column types* are detected and flag the table for review, but are never rewritten — pairs with BLOCKER-4, which is an application rewrite |
| CSQL-DDL-7 | `AUTO_INCREMENT` on a single-column integer PK | info | **Suggestion only**, never applied: a `TISHIFT-REVIEW` comment proposing `AUTO_RANDOM` to avoid a write hotspot on the PK range |

---

## Compatible features — explicitly cleared

Listed so reports show what was *checked and found fine*, not only what failed.

- InnoDB engine (TiDB's only engine)
- Foreign keys — enforced natively since TiDB v6.6. DM's precheck still emits FK
  warnings when the migration is safe; see the FK checklist in `docs/sync-guide.md`
- JSON columns, full JSON path support
- ENUM / SET types
- utf8mb4 charset and `utf8mb4_0900_*` collations
- Window functions and CTEs
- Prepared statements
- Pessimistic transactions (default mode)
- RANGE / LIST / HASH / KEY partitioning
- Online DDL
- Generated columns (VIRTUAL and STORED)
- CHECK constraints
- Read-only views (see WARNING-9 for updatable views)
- GTID-based replication — Cloud SQL enforces GTID, which is what DM prefers

---

## Output format

When reporting, always render three sections in this order — blockers, warnings,
compatible — and include the rule ID on every line. For a consolidated report,
enumerate **every** rule in this file, marking which are backed by a real
collector and which default to "not detected".

## Sources

- [`gcloud sql instances patch`](https://cloud.google.com/sdk/gcloud/reference/sql/instances/patch) — flag semantics, `--database-flags` replace behaviour, `--enable-bin-log`, `--retained-transaction-log-days`
- [`gcloud sql export csv`](https://cloud.google.com/sdk/gcloud/reference/sql/export/csv) — export synopsis and `--offload`
- [Configure database flags for Cloud SQL for MySQL](https://cloud.google.com/sql/docs/mysql/flags) — which flags are configurable
- [Replicating from Cloud SQL to an external server](https://cloud.google.com/sql/docs/mysql/replication/configure-external-replica) — replication user, grants, and the primary-or-standalone requirement
