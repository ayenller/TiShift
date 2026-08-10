# Getting started

## 1. Decide the network path — before anything else

This is the decision that is expensive to revisit, because the option that is
easiest for scanning is the one continue replication cannot use.

| Path | scan / convert / Dumpling | TiDB Cloud DM |
|---|---|---|
| Cloud SQL Auth Proxy | ✅ recommended | ❌ **impossible** |
| Public IP + authorized networks | ✅ | ✅ |
| Private IP + VPC peering / PSC | ✅ | ✅ |

The Auth Proxy runs on *your* machine. DM is a managed service inside TiDB
Cloud with nowhere to run it. If Phase 7 is in your plan, arrange direct
reachability now.

Starting the proxy:

```bash
cloud-sql-proxy --port 3306 PROJECT:REGION:INSTANCE
```

## 2. Create a read-only source user

```sql
CREATE USER 'tishift_ro'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT ON myapp.* TO 'tishift_ro'@'%';
GRANT SELECT ON mysql.user TO 'tishift_ro'@'%';   -- enables the IAM / system-user checks
GRANT REPLICATION CLIENT ON *.* TO 'tishift_ro'@'%';  -- enables topology detection
```

The last two are optional. Without them the scan still completes; the affected
checks report **not detected**, which is not the same as passing.

## 3. Install and configure

```bash
cd cloudsql-to-tidb
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp config/tishift-cloudsql.example.yaml tishift-cloudsql.yaml
```

Edit `tishift-cloudsql.yaml`, then export the passwords it references:

```bash
export TISHIFT_SOURCE_PASSWORD='...'
export TISHIFT_TARGET_PASSWORD='...'
```

`tishift-cloudsql.yaml` is gitignored by the repo root (`tishift*.yaml`). Keep it
that way — it names your instance and your users.

Filling in the `gcp:` section is worth the two minutes: it turns every
remediation in the report from `<INSTANCE>` placeholders into commands you can
paste.

## 4. Run it

```bash
tishift-cloudsql scan --format cli,json,md
tishift-cloudsql scan --continue-replication     # adds the DM readiness checks
```

## Two ways to drive a migration

**AI skill** — `/cloudsql-to-tidb` walks all seven phases interactively, reads
your config file directly, and explains each finding as it goes.

**CLI** — the commands above, for scripting and CI.

Both read the same rule corpus in `references/`.

## Phase guides

- [scan-guide.md](scan-guide.md) — what the scan collects, and what it cannot
- [convert-guide.md](convert-guide.md) — the CSQL-DDL rules and their limits
- [load-guide.md](load-guide.md) — the GCS export chain (manual by design)
- [check-guide.md](check-guide.md) — post-load validation
- [sync-guide.md](sync-guide.md) — DM prechecks and cutover
- [checklist.md](checklist.md) — every rule in one flat list
