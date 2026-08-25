# Load guide

`tishift-cloudsql load` exits 2 on purpose. Bulk data movement is the one
irreversible, hours-long, money-costing step, and a half-finished load leaves a
target that looks populated and is not. Run it yourself, deliberately.

Full decision matrix: [../references/load-strategies.md](../references/load-strategies.md).

## Order of operations

1. Apply the converted schema **first**, wrapped in `SET FOREIGN_KEY_CHECKS=0`/`=1`.
2. Load the data.
3. Add TiFlash replicas **after** the load, if load time matters.
4. Validate ([check-guide.md](check-guide.md)) before pointing anything at the target.

## Chain A — GCS-native (recommended)

One-time grant. The export runs as the **instance's** service account, not
yours; your own bucket access is irrelevant, and this is the most common reason
a first export fails:

```bash
SA=$(gcloud sql instances describe INSTANCE --format='value(serviceAccountEmailAddress)')
gcloud storage buckets add-iam-policy-binding gs://BUCKET \
  --member="serviceAccount:$SA" --role="roles/storage.objectAdmin"
```

Whole-database logical export:

```bash
gcloud sql export sql INSTANCE gs://BUCKET/dump.sql.gz --database=DB --offload
```

Per-table CSV, which is what TiDB Cloud ingests most efficiently and imports in
parallel:

```bash
gcloud sql export csv INSTANCE gs://BUCKET/orders.csv \
  --database=DB --query="SELECT * FROM orders" --offload
```

`--offload` runs the export on a temporary instance instead of straining the
primary. Use it on anything production-facing.

Then grant TiDB Cloud's service account read access — a *different* account from
the one that wrote the export.

**A predefined role is not enough.** `roles/storage.objectViewer` grants
`storage.objects.get` and `storage.objects.list` but **not** `storage.buckets.get`,
which is a bucket-level permission, and the import fails with:

```
Access denied to the source 'gs://BUCKET/': The role doesn't have
storage.buckets.get permission to the source.
```

TiDB Cloud Dedicated requires all three, which means a custom role:

```bash
gcloud iam roles create tidbCloudStorageReader --project=PROJECT \
  --title="TiDB Cloud Storage Reader" \
  --permissions=storage.buckets.get,storage.objects.get,storage.objects.list \
  --stage=GA

gcloud storage buckets add-iam-policy-binding gs://BUCKET \
  --member="serviceAccount:<TIDB_CLOUD_SA>" \
  --role="projects/PROJECT/roles/tidbCloudStorageReader"
```

Find `<TIDB_CLOUD_SA>` on the TiDB Cloud import screen; it looks like
`np-sa-dedicated-prod--XXXXXXXX@tidbcloud-prod-000.iam.gserviceaccount.com`.

Then import in the TiDB Cloud console: **Import → From cloud storage → Google
Cloud Storage**, pointed at the bucket prefix.

Two accounts, two directions, easy to conflate:

| Account | Role | Why |
|---|---|---|
| Cloud SQL instance SA | `roles/storage.objectAdmin` (predefined) | writes the export |
| TiDB Cloud SA | custom: `storage.buckets.get` + `objects.get` + `objects.list` | reads it back |

See [TiDB Cloud external storage docs](https://docs.pingcap.com/tidbcloud/dedicated-external-storage/#configure-gcs-access).

## Chain B — Dumpling over the Auth Proxy (fallback)

When there is no bucket, or the data needs inspecting in flight:

```bash
tiup dumpling -u USER -P 3306 -h 127.0.0.1 --filetype csv -t 8 -o ./dump -B DB
```

Then TiDB Lightning (Dedicated) or `ticloud serverless import start`
(Starter/Essential).

## Choosing

| Tier | Size | Export | Import |
|---|---|---|---|
| Starter | < 25 GiB | `export sql` | GCS import / `ticloud` |
| Essential | any | `export csv` per table | GCS import |
| Dedicated | < ~50 GiB | `export csv` | GCS import |
| Dedicated | large | `export csv`, partitioned by PK range | Lightning, physical mode |
| any | any | Dumpling (no bucket available) | direct replay / Lightning |

`gcloud sql export sql` is mysqldump-based and single-threaded: fine to a few
tens of GiB, painful past that.

## Exclude these

Never migrate the `mysql` schema. It carries `mysql.heartbeat` (written every
second — it will generate endless no-op changes on the target) and the
`cloudsql*` principals, which mean nothing on TiDB. Same for
`information_schema`, `performance_schema`, and `sys`.
