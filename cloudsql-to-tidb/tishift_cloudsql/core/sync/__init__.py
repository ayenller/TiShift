"""Sync phase — continue-replication prechecks. Not implemented yet.

The boundary matters: this module would only ever *verify preconditions* for a
TiDB Cloud DM task (binlog configuration, replication grants, a valid index on
every table, network reachability). It does not create, start, stop, or monitor
the task — that is configured in the TiDB Cloud console.

The Cloud SQL-specific trap this phase exists to catch: the Cloud SQL Auth
Proxy is a local client-side connector, so a source that `scan` reaches happily
through the proxy may be completely unreachable by DM, which is a managed
service and needs direct TCP access. See docs/sync-guide.md.
"""
