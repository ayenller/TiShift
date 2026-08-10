# Scan guide

```bash
tishift-cloudsql scan [--config FILE] [--database SCHEMA] [--continue-replication]
                      [--no-network-path] [--format cli,json,md] [--output-dir DIR] [--quiet]
```

The scan session is set `READ ONLY` at the transaction level before any query
runs, so it cannot mutate the source even by accident.

## What it collects

| Collector | Source | Feeds |
|---|---|---|
| Platform | `@@version`, `@@sql_mode`, `@@lower_case_table_names`, `@@gtid_mode`, … | CSQL-WARNING-3/4, WARNING-8 |
| Users | `mysql.user` | CSQL-WARNING-1 (IAM), CSQL-WARNING-5 (`cloudsql*`) |
| Heartbeat | `information_schema.TABLES` | CSQL-WARNING-5 |
| Topology | `@@read_only`, `SHOW REPLICA STATUS`, `SHOW REPLICAS` | CSQL-BLOCKER-1, CSQL-WARNING-6 |
| Schema | `information_schema.{TABLES,COLUMNS,STATISTICS,…}` | BLOCKER-1..4, 8, 9; WARNING-2..4, 9 |
| DEFINERs | `ROUTINES`/`TRIGGERS`/`EVENTS`/`VIEWS`.`DEFINER` | CSQL-WARNING-2 |
| Binlog | one `SHOW VARIABLES` | CSQL-WARNING-8..13 |
| Valid indexes | `information_schema.statistics` anti-join | replication scoring |

The valid-indexes query only runs with `--continue-replication`; it scans the
whole instance and is pointless for a cutover-only migration.

## Graceful degradation

Missing grants never fail the scan. Without `SELECT` on `mysql.user`, the IAM
and system-user checks report *not detected*. Without `REPLICATION CLIENT`, the
topology reads as *standalone*. This matters: **"not detected" is not
"cleared"**, and the report says so explicitly.

## What it deliberately cannot see

Six rules need query-log analysis this toolkit does not perform — BLOCKER-5
(XA), BLOCKER-6 (UDFs), BLOCKER-7 (XML functions), WARNING-5 (`GET_LOCK`),
WARNING-6 (`SQL_CALC_FOUND_ROWS`), WARNING-7 (`SAVEPOINT`). They are registered
and always reported as *not detected*, so they never silently disappear from a
consolidated report. Confirm them against the application.

The Cloud SQL **edition** (Enterprise vs Enterprise Plus) is not exposed over
the MySQL protocol at all. It is reported as unknown rather than guessed, which
matters because the edition sets the maximum transaction-log retention that
CSQL-WARNING-11 depends on.

## Output

- `tishift-cloudsql-report.json` — the full rule set, always, including rules
  that did not fire
- `tishift-cloudsql-report.md` — human-readable, zero-hit rules omitted
- CLI — same as the markdown, plus per-variable remediation for failing checks

## Reading the score

Five categories, 100 points; see [../references/scoring.md](../references/scoring.md).
The number is not the point — the per-category deductions are, because each one
names the rule that caused it.

Continue-replication deductions only apply with `--continue-replication`. A
cutover-only migration is not penalised for a source with binary logging off.
