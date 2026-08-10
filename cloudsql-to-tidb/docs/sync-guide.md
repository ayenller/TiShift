# Sync guide — continue replication and cutover

**Status: not implemented.** `tishift-cloudsql sync` exits 2. Even when it is
implemented it will only ever *verify preconditions*: the DM task itself is
created, started, monitored, and stopped in the TiDB Cloud console.

Continue replication needs Essential or Dedicated. Starter is cutover-only.

---

## 0. The network problem, first

The Cloud SQL Auth Proxy is a **local, client-side** connector. TiDB Cloud DM is
a managed service with nowhere to run it. If everything so far has gone through
the proxy, this is the moment it stops working.

| Path | scan/export | DM |
|---|---|---|
| Auth Proxy | ✅ | ❌ |
| Public IP + authorized networks | ✅ | ✅ — allowlist TiDB Cloud's DM egress addresses |
| Private IP + VPC peering / PSC | ✅ | ✅ — with matching TiDB Cloud network config |

Google additionally requires that the source of an external replica **be a
primary or standalone instance**. A Cloud SQL read replica cannot be a DM source
at all (CSQL-BLOCKER-1) — point DM at the primary.

## 1. Binlog prechecks

```sql
SHOW VARIABLES WHERE Variable_name IN
 ('log_bin','binlog_format','binlog_row_image','binlog_expire_logs_seconds',
  'binlog_transaction_compression','binlog_row_value_options','server_id',
  'gtid_mode','enforce_gtid_consistency');
```

Or `tishift-cloudsql scan --continue-replication`, which evaluates the same
variables and prints per-variable remediation.

| Variable | Required | How to fix it on Cloud SQL |
|---|---|---|
| `log_bin` | `ON` | `gcloud sql instances patch I --enable-bin-log`. Needs automatic backups on first (`--backup-start-time=HH:MM`) |
| `binlog_format` | `ROW` | **Not configurable.** Cloud SQL sets ROW itself. A different value means binary logging is off — investigate, don't patch |
| `binlog_row_image` | `FULL` | `gcloud sql instances patch I --database-flags=binlog_row_image=full` |
| retention | ≥ 1 day, 7 recommended | `gcloud sql instances patch I --retained-transaction-log-days=7` (range 1–35, ceiling depends on edition) |
| `binlog_row_value_options` | empty | Re-issue `--database-flags` without it, or `--clear-database-flags` |
| `binlog_transaction_compression` | `OFF` | **Not an exposed flag**; off by default. Support case if it reads ON |

Two things that catch everyone:

**`SET GLOBAL` does not work.** No user holds `SUPER` on Cloud SQL, not even
with `cloudsqlsuperuser`. Every one of these is a `gcloud` command or nothing.

**`--database-flags` replaces the whole list.** Omitting a flag you already have
silently clears it. Always:

```bash
gcloud sql instances describe INSTANCE --format='value(settings.databaseFlags)'
```

first, and restate everything you want to keep.

`PARTIAL_JSON` deserves its own warning: DM cannot parse partial-JSON binlog
rows, and the result is **silent corruption of JSON columns**, not a clean
failure.

GTID is enforced ON by Cloud SQL and cannot be turned off. That is good for DM —
resume is position-independent — and it also means `enforce_gtid_consistency`
statement restrictions already apply to your source, so nothing new breaks now.

## 2. Grants

On the source, the replication user Google documents for external replicas:

```sql
CREATE USER 'tishift_dm'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT REPLICATION SLAVE ON *.* TO 'tishift_dm'@'%';
GRANT REPLICATION CLIENT ON *.* TO 'tishift_dm'@'%';
GRANT SELECT ON myapp.* TO 'tishift_dm'@'%';
```

`RELOAD` may not be grantable on a managed instance. If DM's precheck asks for
it, configure the task to rely on GTID rather than a consistent snapshot lock.

On the target, the DM user needs `CREATE`, `SELECT`, `INSERT`, `UPDATE`,
`DELETE`, `ALTER`, `DROP`, and `INDEX` on the destination schema.

## 3. Valid indexes

DM needs a primary key or unique index on every table to apply row changes
deterministically.

```sql
SELECT t.table_schema, t.table_name
FROM information_schema.tables AS t
WHERE (t.table_schema, t.table_name) NOT IN (
        SELECT s.table_schema, s.table_name FROM information_schema.statistics AS s
        WHERE s.NON_UNIQUE = 0 GROUP BY s.table_schema, s.table_name)
  AND t.table_schema NOT IN ('mysql','performance_schema','information_schema','sys')
  AND t.table_type = 'BASE TABLE';
```

Every row is a table that must gain a key before the task starts.

## 4. Task scoping

Set DM's `block-allow-list` to the business schemas only. Two cautions:

- The list is **database-level**, not table-level. A schema you include comes in
  whole.
- Exclude `mysql` explicitly. `mysql.heartbeat` is written roughly every second
  and will otherwise replicate forever as no-op churn (CSQL-WARNING-5).

## 5. Foreign keys

TiDB enforces foreign keys natively (v6.6+), but DM's precheck still emits FK
warnings on migrations that are perfectly safe. Before dismissing them, confirm:

1. Every referenced table is inside the task's scope.
2. Parent tables are created before children, or the apply is wrapped in
   `SET FOREIGN_KEY_CHECKS=0`.
3. No FK references a table in an excluded schema.
4. `ON DELETE` / `ON UPDATE` actions are ones TiDB supports.
5. The initial load completed before incremental sync started.

## 6. Validating the task actually works

Before trusting it, prove it end to end with a tagged row:

```sql
-- source
INSERT INTO myapp.customers (email, full_name) VALUES ('TISHIFT_TEST_1@example.com', 'TISHIFT_TEST');
-- target, a few seconds later
SELECT * FROM myapp.customers WHERE email LIKE 'TISHIFT\_TEST\_%';
-- then clean up on the source and confirm the delete replicates too
```

The tag makes the probe rows trivial to find and remove afterwards.

## 7. Cutover checklist

Executing the cutover is your call, not this tool's.

- [ ] DM replication lag is near zero and stable
- [ ] Row counts and checksums match ([check-guide.md](check-guide.md))
- [ ] Application credentials exist on TiDB — including replacements for every
      IAM database user
- [ ] Stop writes to Cloud SQL, and confirm they have stopped
- [ ] Wait for lag to reach zero
- [ ] Repoint the application
- [ ] Decide the fate of every downstream Cloud SQL read replica
      (CSQL-WARNING-6) — one left running is a stale read path
- [ ] Keep the source intact and read-only for the rollback window
- [ ] Stop the DM task only after the rollback window closes
