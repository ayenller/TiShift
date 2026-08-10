"""Load phase — intentionally not automated.

Bulk data movement is the one irreversible, high-stakes step in a migration:
it takes hours, it costs money in egress, and a half-finished load leaves a
target that looks populated but is not. This tool does not run it, and the
`tishift-cloudsql load` command exits non-zero rather than pretending to.

The chosen chain for Cloud SQL is GCS-native — `gcloud sql export` into a
bucket, then TiDB Cloud's "Import from cloud storage" — with Dumpling over the
Cloud SQL Auth Proxy as the fallback when no bucket is available. Both are
written up, with a tier x data-size decision matrix, in docs/load-guide.md and
references/load-strategies.md.
"""
