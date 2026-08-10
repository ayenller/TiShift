# Cloud SQL → TiDB migration checklist

The flat version. Everything the phase guides cover, in one list, if you would
rather not walk the phases.

---

## 1. Before you touch anything

- [ ] **Pick the network path.** Auth Proxy works for scanning and export and is
      *unusable* by TiDB Cloud DM. If continue replication is in the plan, you
      need public IP + authorized networks, or private IP + VPC peering / PSC.
- [ ] **Pick the target tier.** It changes the FULLTEXT answer (real indexes are
      Starter-only), the capacity cap (Starter is 25 GiB), and whether continue
      replication is possible at all (not on Starter).
- [ ] **Check whether the source is a read replica.** It cannot be a DM source
      (CSQL-BLOCKER-1) — use the primary.
- [ ] Create a read-only source user; add `SELECT` on `mysql.user` and
      `REPLICATION CLIENT` if you want the IAM and topology checks to work.

## 2. The Cloud SQL facts that change how everything is fixed

- [ ] **`SET GLOBAL` fails.** No user holds `SUPER`, including with
      `cloudsqlsuperuser`. Every variable is `gcloud sql instances patch`, an
      instance setting, or not changeable.
- [ ] **`--database-flags` REPLACES the entire flag list.** Run
      `gcloud sql instances describe I --format='value(settings.databaseFlags)'`
      first and restate every flag you are keeping.
- [ ] **Binary logging is not a flag** — `--enable-bin-log`, and it needs
      automatic backups already on.
- [ ] **`binlog_format` is not configurable.** Cloud SQL forces `ROW`.
- [ ] **`lower_case_table_names` is immutable** after instance creation.
- [ ] **`gcloud sql export` runs as the instance's service account**, not yours.

## 3. Blockers

| ID | Check |
|---|---|
| BLOCKER-1 | Stored procedures — rewrite as application code |
| BLOCKER-2 | Triggers — move to application middleware |
| BLOCKER-3 | Events — move to Cloud Scheduler / cron |
| BLOCKER-4 | Spatial columns — convert to JSON, rewrite the geometry logic |
| BLOCKER-5 | XA transactions — *not detected by this tool*, confirm manually |
| BLOCKER-6 | UDFs — *not detected*, confirm manually |
| BLOCKER-7 | XML functions — *not detected*, confirm manually |
| BLOCKER-8 | Charsets outside ascii/latin1/binary/utf8/utf8mb4/gbk |
| BLOCKER-9 | Table names colliding once case is folded — rename; unfixable on the source |
| CSQL-BLOCKER-1 | Source is a read replica and replication is planned |

## 4. Warnings

| ID | Check |
|---|---|
| WARNING-2 | FULLTEXT indexes outside Starter — TiFlash replica stands in |
| WARNING-3 | AUTO_INCREMENT is unique but not sequential |
| WARNING-4 | `utf8mb4_0900_*` — supported, no action |
| WARNING-5/6/7 | `GET_LOCK`, `SQL_CALC_FOUND_ROWS`, `SAVEPOINT` — *not detected*, confirm manually |
| WARNING-8 | `lower_case_table_names != 2` |
| WARNING-9 | Updatable views — TiDB views are read-only |
| CSQL-WARNING-1 | IAM database users — no TiDB equivalent, reissue credentials |
| CSQL-WARNING-2 | DEFINERs naming Cloud SQL / IAM principals |
| CSQL-WARNING-3 | MySQL 5.7 source |
| CSQL-WARNING-4 | Source `sql_mode` laxer than TiDB's default |
| CSQL-WARNING-5 | `mysql.heartbeat` and `cloudsql*` users in scope |
| CSQL-WARNING-6 | Downstream read replicas |
| CSQL-WARNING-7 | Non-InnoDB tables |

## 5. Continue-replication prechecks (Phase 7 only)

| ID | Variable | Required |
|---|---|---|
| CSQL-WARNING-8 | `log_bin` | `ON` |
| CSQL-WARNING-9 | `binlog_format` | `ROW` (not configurable) |
| CSQL-WARNING-10 | `binlog_row_image` | `FULL` |
| CSQL-WARNING-11 | retention | ≥ 86400s, 604800s recommended |
| CSQL-WARNING-12 | `binlog_row_value_options` | empty — `PARTIAL_JSON` corrupts JSON silently |
| CSQL-WARNING-13 | `binlog_transaction_compression` | `OFF` |

- [ ] Replication user: `REPLICATION SLAVE`, `REPLICATION CLIENT`, `SELECT`
- [ ] Every table has a PK or unique index
- [ ] `block-allow-list` scoped to business schemas; `mysql` excluded
- [ ] FK checklist reviewed (see [sync-guide.md](sync-guide.md) §5)
- [ ] DM task validated with a `TISHIFT_TEST_`-tagged row

## 6. Convert

| Rule | Action |
|---|---|
| CSQL-DDL-1 | `DEFINER` commented out |
| CSQL-DDL-2 | Non-InnoDB engine → InnoDB |
| CSQL-DDL-3 | Storage/statistics table options commented out |
| CSQL-DDL-4 | utf8/utf8mb3 → utf8mb4 |
| CSQL-DDL-5 | FULLTEXT kept + TiFlash replica |
| CSQL-DDL-6 | SPATIAL index commented out |
| CSQL-DDL-7 | AUTO_RANDOM suggested, never applied |

- [ ] Apply wrapped in `SET FOREIGN_KEY_CHECKS=0` / `=1` — otherwise `ERROR 1824`
- [ ] Zero parse errors in the cleanup report

## 7. Load

- [ ] Schema applied before any data
- [ ] Bucket IAM granted to the **instance's** service account
- [ ] `--offload` used on production-facing exports
- [ ] `mysql`, `sys`, `performance_schema`, `information_schema` excluded
- [ ] TiFlash replicas added after the load, if load time matters

## 8. Validate

- [ ] Row counts
- [ ] Column structure diff (expect the deliberate CSQL-DDL differences)
- [ ] `BIT_XOR(CRC32(…))` checksums over matching PK ranges
- [ ] `information_schema.tiflash_replica` → `AVAILABLE = 1`
- [ ] Application users exist on TiDB, IAM users replaced

## 9. Cutover

- [ ] Lag near zero and stable
- [ ] Writes stopped on Cloud SQL and confirmed stopped
- [ ] Application repointed
- [ ] Downstream Cloud SQL replicas dealt with
- [ ] Source kept read-only for the rollback window

## 10. Scoring quick reference

| Category | Max |
|---|---|
| Schema compatibility | 30 |
| Programmable objects | 25 |
| Cloud SQL platform surface | 20 |
| Data & load feasibility | 15 |
| Cutover & continue replication | 10 |

85–100 READY · 65–84 READY WITH WORK · 40–64 SIGNIFICANT REWORK · 0–39 NOT
RECOMMENDED YET

## 11. What this tool cannot see

Six rules need query-log analysis that is not implemented: BLOCKER-5, BLOCKER-6,
BLOCKER-7, WARNING-5, WARNING-6, WARNING-7. They always report **not detected**.
The Cloud SQL **edition** is not exposed over the MySQL protocol and is reported
as unknown. None of these are the same as "cleared".
