# cloudsql-to-tidb

Google Cloud SQL for MySQL → TiDB Cloud migration module for TiShift.

Cloud SQL for MySQL *is* MySQL, so the schema-level work is small. What makes
these migrations stall is the **managed-platform layer**, and that is what this
module concentrates on:

- **`SET GLOBAL` does not work.** No user holds `SUPER` — not even one granted
  `cloudsqlsuperuser` — so every server-variable fix is a `gcloud sql instances
  patch`, an instance setting, or impossible. Each finding carries the right
  command for its variable.
- **Binary logging is not a flag.** It is a side effect of enabling automatic
  backups plus `--enable-bin-log`, and retention is governed by the instance's
  transaction-log retention days, not by raising `binlog_expire_logs_seconds`.
- **`binlog_format` is not user-configurable.** Cloud SQL forces `ROW`. If it
  reads anything else, patching is the wrong response.
- **The Auth Proxy cannot be used by TiDB Cloud DM.** It is a local client-side
  connector. A migration that scans cleanly through the proxy can still be
  completely unreachable for continue replication.
- **A Cloud SQL read replica cannot be a DM source at all** — Google requires a
  primary or standalone instance.
- **IAM database users have no TiDB equivalent**, and `DEFINER` clauses naming
  Cloud SQL principals make the converted DDL fail to apply.
- **`lower_case_table_names` is immutable** after instance creation, so a
  case-collision blocker can only be fixed by renaming tables.

## How it works

| Phase | What happens | Automated by |
|---|---|---|
| 1. Connect | Confirm the network path and the target tier | you (SKILL.md Phase 1) |
| 2. Scan | Platform metadata, schema inventory, binlog precheck | `tishift-cloudsql scan` |
| 3. Assess & Score | Compatibility rules → 0-100 readiness score | `tishift-cloudsql scan` |
| 4. Convert schema | CSQL-DDL-1..7 rewrites, TiFlash replicas | `tishift-cloudsql convert` |
| 5. Load data | `gcloud sql export` → GCS → TiDB Cloud import | **you** — deliberately not automated |
| 6. Validate | Row counts, structure diff, checksums | documented, not yet automated |
| 7. Continue replication & cutover | DM prechecks, then cutover | documented, not yet automated |

Loading and cutover keep a human in the loop on purpose. They are the two
irreversible steps, and a tool that half-finishes either one leaves a target
that looks right and is not.

## Prerequisites

- A network path to the instance: Cloud SQL Auth Proxy (recommended for
  scanning), or public IP with your address in the instance's authorized
  networks, or private IP over VPC peering / Private Service Connect.
- A read-only source user with `SELECT` on the migrated schema. `SELECT` on
  `mysql.user` additionally enables the IAM/system-user checks; without it those
  degrade to "not detected" rather than failing.
- A TiDB Cloud cluster and a decision about its tier — the tier changes the
  FULLTEXT, capacity, and continue-replication answers.
- For Phase 7: automatic backups and binary logging enabled on the instance, and
  a network path DM can actually use.
- Python 3.10+, and `gcloud` for the export path.

## Implementation status

Implemented: `scan`, `convert`.
Stubbed and documented: `check`, `sync` — both exit non-zero rather than
pretending to succeed.
Deliberately disabled: `load`.
Out of scope: executing the cutover.

### DDL cleanup rules (convert phase)

| Rule | Trigger | Action |
|---|---|---|
| CSQL-DDL-1 | `DEFINER=…` | commented out |
| CSQL-DDL-2 | `ENGINE=MyISAM\|MEMORY\|ARCHIVE\|CSV` | rewritten to InnoDB |
| CSQL-DDL-3 | `ROW_FORMAT`, `KEY_BLOCK_SIZE`, `ENCRYPTION`, `COMPRESSION`, `TABLESPACE`, `STATS_*` | commented out |
| CSQL-DDL-4 | `utf8`/`utf8mb3` charset or collation | widened to utf8mb4 + review note |
| CSQL-DDL-5 | `FULLTEXT KEY` | kept (parse-only) + TiFlash replica emitted |
| CSQL-DDL-6 | `SPATIAL KEY` | commented out + review note |
| CSQL-DDL-7 | `AUTO_INCREMENT` single-column PK | suggestion only — never applied |

Nothing is deleted: originals survive in `TISHIFT-REMOVED` comments, and running
convert over its own output is a no-op.

## AI skill

`/cloudsql-to-tidb` walks the whole migration interactively. It prefers
**config-file mode**: with a filled-in `tishift-cloudsql.yaml` it reads
connection details from the file and drives the phases directly, rather than
asking you to paste command output back one step at a time.

`docs/checklist.md` is the flat, all-rules reference if you would rather not
walk the phases.

## CLI toolkit

```bash
cd cloudsql-to-tidb
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp config/tishift-cloudsql.example.yaml tishift-cloudsql.yaml
# edit tishift-cloudsql.yaml, then export the passwords it references
export TISHIFT_SOURCE_PASSWORD=... TISHIFT_TARGET_PASSWORD=...

# Phase 2+3 — scan, assess, score
tishift-cloudsql scan --format cli,json,md

# Include DM readiness in the findings and the score
tishift-cloudsql scan --continue-replication

# Phase 4 — convert schema
mysqldump --no-data --routines --triggers -h 127.0.0.1 -u USER DB > source-schema.sql
tishift-cloudsql convert --ddl-file source-schema.sql --dry-run
tishift-cloudsql convert --ddl-file source-schema.sql

# Phase 5 — refuses to run, by design
tishift-cloudsql load
```

Start the Auth Proxy first if that is your path:

```bash
cloud-sql-proxy --port 3306 PROJECT:REGION:INSTANCE
```

### Gotchas worth knowing before you start

- `gcloud sql instances patch --database-flags` **replaces** the entire flag
  list. Run `gcloud sql instances describe <INSTANCE>
  --format='value(settings.databaseFlags)'` first and restate every flag you
  want to keep, or you will silently clear them.
- `gcloud sql export` runs as the *instance's* service account, not yours. Grant
  it `roles/storage.objectAdmin` on the bucket or the first export fails.
- Use `gcloud sql export --offload` on anything production-facing; it runs the
  export on a temporary instance instead of straining the primary.
- Apply converted DDL wrapped in `SET FOREIGN_KEY_CHECKS=0` / `=1` — per-table
  DDL is not FK-topologically ordered and dies with `ERROR 1824` otherwise.

## Layout

```
cloudsql-to-tidb/
├── SKILL.md                    # the AI runbook — the primary deliverable
├── config/                     # tishift-cloudsql.example.yaml
├── docs/                       # per-phase operator guides + checklist.md
├── references/                 # canonical rule corpus, mirrored by rules/*.py
├── sql/sample-schema.sql       # fixture that triggers every rule
├── tests/                      # offline, no database required
└── tishift_cloudsql/
    ├── rules/                  # declarative rule registries (data)
    ├── core/                   # engines (behaviour): scan, convert, stubs
    └── gcloud.py               # renders the gcloud commands quoted in reports
```

## Tests

```bash
pytest tests -q
```

Every test runs offline against scripted fixtures — no database, no GCP project.
