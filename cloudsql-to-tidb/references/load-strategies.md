# Load strategies — Cloud SQL for MySQL → TiDB Cloud

The toolkit does **not** run any of this. `tishift-cloudsql load` exits non-zero
on purpose: bulk data movement is the one irreversible, hours-long, money-costing
step, and a half-finished load leaves a target that looks populated but is not.

Two chains. Prefer the first.

## Chain A — GCS-native (recommended)

`gcloud sql export` → GCS bucket → TiDB Cloud "Import from cloud storage".

Both ends already speak GCS, so the data never lands on an operator's laptop and
there is no long-running local process to babysit.

### One-time setup: grant the *instance's* service account bucket access

The export runs as the Cloud SQL instance's service account, not as you. Your
own bucket access is irrelevant, and this is the most common reason a first
export fails:

```bash
SA=$(gcloud sql instances describe INSTANCE --format='value(serviceAccountEmailAddress)')
gcloud storage buckets add-iam-policy-binding gs://BUCKET \
  --member="serviceAccount:$SA" --role="roles/storage.objectAdmin"
```

### Logical export (whole database, one file)

```bash
gcloud sql export sql INSTANCE gs://BUCKET/dump.sql.gz --database=DB --offload
```

`--offload` runs the export on a temporary instance so the primary is not
strained — worth it on anything production-facing. The export is mysqldump-based
and single-threaded, which is fine up to a few tens of GiB and painful past that.

### CSV export (per table, parallelisable)

```bash
gcloud sql export csv INSTANCE gs://BUCKET/orders.csv \
  --database=DB --query="SELECT * FROM orders" --offload
```

One command per table. This is the format TiDB Cloud's importer ingests most
efficiently, and separate files import in parallel.

## Chain B — Dumpling over the Auth Proxy (fallback)

When there is no bucket to write to, or the data has to be inspected or
transformed in flight:

```bash
tiup dumpling -u USER -P 3306 -h 127.0.0.1 \
  --filetype csv -t 8 -o ./dump -B DB
```

Runs against the Auth Proxy's loopback socket. Then load with TiDB Lightning
(Dedicated) or `ticloud serverless import start` (Starter/Essential).

## Decision matrix

| Tier | Data size | Export | Import |
|---|---|---|---|
| Starter | < 25 GiB (the tier cap) | `gcloud sql export sql` | TiDB Cloud import from GCS, or `ticloud serverless import start` |
| Essential | any | `gcloud sql export csv`, one file per table | TiDB Cloud "Import from cloud storage" |
| Dedicated | < ~50 GiB | `gcloud sql export csv` | TiDB Cloud import from GCS |
| Dedicated | large | `gcloud sql export csv`, partitioned by PK range | TiDB Lightning, physical mode |
| any | any, no bucket available | Dumpling over the Auth Proxy | direct replay / Lightning |

## Order of operations

1. Apply the converted schema **first** (`tishift-cloudsql convert`, then apply
   the output). Importing into tables that do not exist yet fails; importing
   into tables TiDB inferred from CSV headers gets you the wrong types.
2. Wrap the schema apply in `SET FOREIGN_KEY_CHECKS=0` … `SET FOREIGN_KEY_CHECKS=1`.
   Per-table `SHOW CREATE TABLE` output is not FK-topologically ordered, so an
   unwrapped apply dies with `ERROR 1824` on the first forward reference.
3. Load the data.
4. Add TiFlash replicas **after** the load, not before — replica maintenance
   during a bulk load slows the load down. The convert phase emits the `ALTER`
   statements inline; move them to the end if load time matters.
5. Validate (`docs/check-guide.md`) before pointing any application at the target.

## TiFlash and the analytics workload

Cloud SQL Enterprise Plus's data cache is a row-store accelerator, not a
columnar engine, so there is no direct equivalent to port. If analytics queries
were the reason for Enterprise Plus, TiFlash replicas are the TiDB answer and
should be sized deliberately rather than inherited from this tool's defaults.
