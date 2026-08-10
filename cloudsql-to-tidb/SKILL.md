---
name: cloudsql-to-tidb-migration
description: Migrate Google Cloud SQL for MySQL databases to TiDB — assess readiness, convert schema, load data, and validate. Use this skill whenever someone mentions migrating from Cloud SQL, GCP MySQL, or Google Cloud SQL to TiDB, wants to assess Cloud SQL compatibility with TiDB, needs to convert Cloud SQL schema to TiDB DDL, or is planning any Cloud SQL to TiDB migration project, even if they don't use the word "migration" explicitly.
metadata:
  version: 0.1.0
---

# Cloud SQL for MySQL to TiDB Migration

Cloud SQL for MySQL *is* MySQL, so the schema work is usually small. The work
that actually stalls these migrations lives in the **managed-platform layer**,
and that is where you should spend your attention:

- `SET GLOBAL` fails on everything — no user has `SUPER`, not even with
  `cloudsqlsuperuser`. Every fix is a `gcloud` command or impossible.
- Binary logging is not a database flag; it is an instance setting that depends
  on automatic backups.
- `binlog_format` is not user-configurable at all.
- The Cloud SQL Auth Proxy cannot be used by TiDB Cloud DM.
- A Cloud SQL read replica cannot be a DM source.
- IAM database users and `DEFINER` clauses naming Cloud SQL principals have no
  TiDB equivalent.
- `lower_case_table_names` is immutable after instance creation.

`docs/checklist.md` is the flat all-rules reference if the user wants everything
at once instead of walking the phases.

---

## How to use this skill

### Config-file mode — preferred

If a filled-in `tishift-cloudsql.yaml` exists (or the user points you at one),
**read the connection details from it and drive the phases directly**. Run the
commands yourself, show the user the results, and keep going. Do not make them
paste output back step by step when the config already tells you how to connect.

Confirm once, early, that the config is gitignored (the repo root ignores
`tishift*.yaml`). Never echo passwords or the resolved values of `${ENV_VAR}`
placeholders.

If no config exists, offer to create one from
`config/tishift-cloudsql.example.yaml` and ask for the handful of values it
needs. That is almost always faster than the fallback.

### Call-and-response fallback

When there is no config and the user does not want one — or when you have no
ability to run commands — output **one command at a time**, say "run this and
paste the output", and wait. Migrations are high-stakes and each step deserves
a human look before the next one.

### Security

Have the user `export MYSQL_PWD=...` rather than putting passwords on command
lines. Ask for *results*, never for the commands with credentials in them.

### Reporting

- **Always show the full generated report.** Echo the whole `.md` into the
  conversation. Do not paraphrase it into three bullets.
- **When asked for a consolidated report, enumerate every check**, not only the
  ones that fired — and say which are backed by a real collector and which
  default to "not detected". BLOCKER-5/6/7 and WARNING-5/6/7 need query-log
  analysis nobody implemented; the Cloud SQL edition is not visible over the
  MySQL protocol. "Not detected" is not "cleared", and reporting it as cleared
  is the most damaging thing this skill can do.

---

## Phase 1: Connect

**Ask the target tier first.** It changes the answer to almost everything
downstream: FULLTEXT (real indexes are Starter-only), capacity (Starter caps at
25 GiB), and whether continue replication is possible at all (it is not, on
Starter).

**Then settle the network path**, because the convenient option for scanning is
the one DM cannot use:

| Path | scan / convert / export | TiDB Cloud DM |
|---|---|---|
| Cloud SQL Auth Proxy | ✅ recommended | ❌ impossible — it is a local client-side connector |
| Public IP + authorized networks | ✅ | ✅ |
| Private IP + VPC peering / PSC | ✅ | ✅ |

If the user wants continue replication, arrange direct reachability *now*.
Discovering this at cutover means redoing network design under time pressure.

### 1.1 Source identity

```bash
mysql -h 127.0.0.1 -P 3306 -u USER -e "SELECT @@version, @@version_comment, @@sql_mode, @@lower_case_table_names"
```

`@@version_comment` containing `(Google)` confirms Cloud SQL. A `5.7.x` version
is CSQL-WARNING-3.

### 1.2 Topology

```bash
mysql -h 127.0.0.1 -P 3306 -u USER -e "SHOW REPLICA STATUS\G SHOW REPLICAS"
```

If `SHOW REPLICA STATUS` returns a row, this instance is a replica —
**CSQL-BLOCKER-1** for any replication plan. Point at the primary instead.

### 1.3 Target

```bash
mysql -h TIDB_HOST -P 4000 -u root --ssl-mode=VERIFY_IDENTITY -e "SELECT VERSION()"
```

**Gate:** tier chosen, network path chosen, both endpoints reachable.

---

## Phase 2: Scan

With a config, this is one command:

```bash
tishift-cloudsql scan --format cli,json,md
tishift-cloudsql scan --continue-replication    # adds DM readiness to findings and score
```

Without one, walk the steps in `docs/scan-guide.md`. Either way you are
collecting: platform variables, `mysql.user` (IAM and `cloudsql*` principals),
`mysql.heartbeat`, replication topology, the full schema inventory, every
object's `DEFINER`, the binlog variables, and — only when replicating — tables
without a valid index.

Note which collectors could not run. A source user without `SELECT` on
`mysql.user` cannot see IAM users; without `REPLICATION CLIENT` it cannot see
topology. Say so rather than reporting a clean result.

**Gate:** the full checklist is assembled and the gaps are named.

---

## Phase 3: Assess & Score

Load `references/compatibility-rules.md` and `references/scoring.md`.

Present, in this order:

1. **Blockers** — table of rule ID, feature, count, action.
2. **Warnings** — same shape.
3. **The score** — 0–100 with the per-category breakdown. The number is not the
   point; the deductions are, because each names its rule.
4. **The Cloud SQL platform story, explicitly.** This is the part a generic
   MySQL assessment would miss: which variables cannot be changed, which
   principals cannot be migrated, and what that means for their timeline.

Categories: Schema compatibility 30, Programmable objects 25, Cloud SQL platform
surface 20, Data & load feasibility 15, Cutover & continue replication 10.

**Gate:** the user has acknowledged the blockers and decided what to do about
each one.

---

## Phase 4: Convert Schema

### 4.1 Extract

```bash
mysqldump --no-data --routines --triggers --events -h 127.0.0.1 -u USER myapp > source-schema.sql
```

### 4.2 Convert

```bash
tishift-cloudsql convert --ddl-file source-schema.sql --dry-run
tishift-cloudsql convert --ddl-file source-schema.sql
```

Rules CSQL-DDL-1..7 (`docs/convert-guide.md`). Nothing is deleted — originals
survive in `TISHIFT-REMOVED` comments — and re-running is a no-op.

### 4.3 Disclose the limits

Three of these rewrites are not the whole job, and saying so is part of the work:

- **CSQL-DDL-2** changes `ENGINE=MyISAM` to InnoDB. It does not change the fact
  that MyISAM was non-transactional. If the application depended on that, this
  is the start of the work.
- **CSQL-DDL-4** widens utf8 to utf8mb4. Sort order changes and index keys grow
  from 3 to 4 bytes per character.
- **CSQL-DDL-6** comments out spatial indexes but leaves the column types alone.
  Converting them to JSON and moving geometry logic into the application is
  BLOCKER-4 — a rewrite, not a conversion.

### 4.4 TiFlash

Outside Starter, TiDB parses `FULLTEXT KEY` and builds no index. Convert emits
`ALTER TABLE … SET TIFLASH REPLICA n` so columnar scans accelerate
`LIKE`/`REGEXP` filtering instead. Tell the user two things: `MATCH … AGAINST`
still needs rewriting, and replicas created before the load slow the load down.

### 4.5 Apply

```sql
SET FOREIGN_KEY_CHECKS=0;
SOURCE converted-schema.sql;
SET FOREIGN_KEY_CHECKS=1;
```

Per-table DDL is not FK-topologically ordered; unwrapped it dies with
`ERROR 1824`.

**Gate:** zero parse errors, schema applied, user has seen the review items.

---

## Phase 5: Load Data — NOT handled by this skill

Tell the user, in substance:

> Bulk data loading is the one irreversible, hours-long step of this migration,
> and it is deliberately outside what this tooling does. `docs/load-guide.md`
> has the full path: `gcloud sql export` into a GCS bucket, then TiDB Cloud's
> "Import from cloud storage". Run it yourself and tell me when it has
> finished, and I will pick up at validation.

Three things worth flagging before they start:

- The export runs as the **instance's** service account, not theirs. Grant it
  `roles/storage.objectAdmin` on the bucket or the first export fails.
- Use `--offload` on anything production-facing.
- Exclude `mysql` — `mysql.heartbeat` and the `cloudsql*` users must not travel.

**Gate:** the user confirms the load finished. Do not proceed on an assumption.

---

## Phase 6: Validate

Per `docs/check-guide.md`: row counts, column structure diff, `BIT_XOR(CRC32(…))`
checksums over matching PK ranges, and `information_schema.tiflash_replica`
showing `AVAILABLE = 1`.

Expect and confirm the deliberate structure differences: `utf8`→`utf8mb4`,
`MyISAM`→`InnoDB`, missing spatial indexes. Anything else is a defect.

Also check that application users exist on the target — every IAM database user
needs a password-based replacement before cutover.

**Gate:** counts and checksums agree, or the differences are understood.

---

## Phase 7: Continue Replication & Cutover

Essential or Dedicated only. Full runbook in `docs/sync-guide.md`.

Preflight, in order:

1. **Network.** DM cannot use the Auth Proxy. Confirm the real path exists.
2. **Not a replica.** CSQL-BLOCKER-1.
3. **Binlog variables.** And remember every remediation is a `gcloud` command:
   `--enable-bin-log` for `log_bin` (needs backups on), `--database-flags` for
   `binlog_row_image`, `--retained-transaction-log-days` for retention. Warn
   that `--database-flags` **replaces the entire list** — describe first.
   `binlog_format` is not configurable; if it is not `ROW`, investigate rather
   than patch.
4. **`PARTIAL_JSON`.** If `binlog_row_value_options` is set, DM corrupts JSON
   columns silently. This is not a warning to skim past.
5. **Grants.** `REPLICATION SLAVE`, `REPLICATION CLIENT`, `SELECT`. `RELOAD` may
   not be grantable — fall back to GTID-based resume.
6. **Valid indexes.** Every table needs a PK or unique key.
7. **Scope.** `block-allow-list` is database-level; exclude `mysql` explicitly.
8. **Foreign keys.** DM's precheck warns even when the migration is safe — walk
   the five-item checklist in `docs/sync-guide.md` §5 before dismissing.
9. **Prove it.** Insert a `TISHIFT_TEST_`-tagged row, watch it arrive, delete it,
   watch the delete arrive.

Then the cutover checklist — including deciding the fate of every downstream
Cloud SQL read replica, since one left running is a stale read path.

**Executing the cutover is the user's call, not this skill's.**

---

## Re-running on a live migration

If asked to "re-check" after a DM task already exists, do **not** drop or
recreate the target schema. Re-run only the read-only and idempotent parts:
`scan --continue-replication`, `convert --dry-run`, and the Phase 6 queries.

---

## Decision points

| Question | Ask when | Drives |
|---|---|---|
| Which tier? | Phase 1, first | FULLTEXT, capacity, replication feasibility |
| Cutover-only or continue replication? | Phase 1 | Whether binlog rules gate at all, and the network path |
| Network path? | Phase 1 | Whether Phase 7 is possible without rework |
| GCS bucket available? | Phase 5 | Export chain A vs Dumpling fallback |
| Keep MyISAM semantics? | Phase 4 | Whether CSQL-DDL-2 is sufficient |

## Reference files

- `references/compatibility-rules.md` — every BLOCKER / WARNING / CSQL rule, and
  the DDL cleanup rules
- `references/type-mapping.md` — type, table-option, and index mappings; the
  MySQL 5.7 differences
- `references/load-strategies.md` — the GCS chain, the Dumpling fallback, and
  the tier × size decision matrix
- `references/scoring.md` — categories, deductions, rating bands
- `docs/checklist.md` — all of the above, flat
