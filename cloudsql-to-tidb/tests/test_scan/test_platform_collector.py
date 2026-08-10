from __future__ import annotations

from tishift_cloudsql.core.scan.collectors.platform import collect_platform_metadata
from tests.test_scan.fake_connection import RAISES_ERROR, ScriptedConnection

# Order matters: substring matching means the longer variable name has to be
# registered before the one it contains (@@version_comment before @@version).
BASE_RESPONSES: list[tuple[str, object]] = [
    ("@@version_comment", [{"@@version_comment": "(Google)"}]),
    ("@@version", [{"@@version": "8.0.37-google"}]),
    ("@@sql_mode", [{"@@sql_mode": "STRICT_TRANS_TABLES,NO_ZERO_DATE"}]),
    ("@@character_set_server", [{"@@character_set_server": "utf8mb4"}]),
    ("@@collation_server", [{"@@collation_server": "utf8mb4_0900_ai_ci"}]),
    ("@@transaction_isolation", [{"@@transaction_isolation": "REPEATABLE-READ"}]),
    ("@@local_infile", [{"@@local_infile": "0"}]),
    ("@@binlog_row_value_options", [{"@@binlog_row_value_options": ""}]),
    ("@@enforce_gtid_consistency", [{"@@enforce_gtid_consistency": "ON"}]),
    ("@@gtid_mode", [{"@@gtid_mode": "ON"}]),
    ("@@lower_case_table_names", [{"@@lower_case_table_names": "0"}]),
    ("@@max_connections", [{"@@max_connections": "4000"}]),
    ("@@cloudsql_iam_authentication", [{"@@cloudsql_iam_authentication": "off"}]),
    ("@@super_read_only", [{"@@super_read_only": "0"}]),
    ("@@read_only", [{"@@read_only": "0"}]),
    (
        "FROM mysql.user",
        [
            {"user": "cloudsqladmin", "host": "localhost", "plugin": "mysql_native_password"},
            {"user": "cloudsqlreplica", "host": "%", "plugin": "mysql_native_password"},
            {"user": "appuser", "host": "%", "plugin": "mysql_native_password"},
            {"user": "svc@p.iam", "host": "%", "plugin": "cloudsql_iam_authentication"},
        ],
    ),
    ("TABLE_NAME = 'heartbeat'", [{"n": 1}]),
    ("SHOW REPLICA STATUS", []),
    ("SHOW REPLICAS", []),
]


def _collect(overrides: list[tuple[str, object]] | None = None):
    responses = list(overrides or []) + BASE_RESPONSES
    return collect_platform_metadata(ScriptedConnection(responses))


def test_collects_version_and_detects_cloudsql() -> None:
    meta = _collect()
    assert meta.mysql_version == "8.0.37-google"
    assert meta.is_cloudsql


def test_plain_mysql_is_not_flagged_as_cloudsql() -> None:
    meta = _collect([("@@version_comment", [{"@@version_comment": "MySQL Community Server"}])])
    assert not meta.is_cloudsql


def test_edition_stays_unknown() -> None:
    # Not exposed over the MySQL protocol — must not be guessed.
    assert _collect().edition is None


def test_separates_system_users_from_iam_users() -> None:
    meta = _collect()
    assert meta.cloudsql_system_users == ["cloudsqladmin", "cloudsqlreplica"]
    assert meta.iam_users == ["svc@p.iam@%"]
    assert meta.iam_authentication_enabled


def test_ordinary_users_are_neither() -> None:
    meta = _collect()
    assert "appuser" not in meta.cloudsql_system_users
    assert not any("appuser" in u for u in meta.iam_users)


def test_iam_flag_on_without_iam_users() -> None:
    meta = _collect(
        [
            ("@@cloudsql_iam_authentication", [{"@@cloudsql_iam_authentication": "on"}]),
            ("FROM mysql.user", [{"user": "appuser", "host": "%", "plugin": "mysql_native_password"}]),
        ]
    )
    assert meta.iam_authentication_enabled
    assert meta.iam_users == []


def test_missing_mysql_user_grant_degrades_quietly() -> None:
    # A read-only account without SELECT on mysql.user is common; the scan must
    # still complete, with the checks reported as "not detected".
    meta = _collect([("FROM mysql.user", RAISES_ERROR)])
    assert meta.cloudsql_system_users == []
    assert meta.iam_users == []
    assert meta.mysql_version == "8.0.37-google"


def test_detects_heartbeat_table() -> None:
    assert _collect().has_heartbeat_table
    assert not _collect([("TABLE_NAME = 'heartbeat'", [{"n": 0}])]).has_heartbeat_table


def test_standalone_topology() -> None:
    meta = _collect()
    assert not meta.is_replica
    assert meta.connected_replica_count == 0


def test_detects_read_replica_source() -> None:
    meta = _collect([("SHOW REPLICA STATUS", [{"Source_Host": "10.0.0.9"}])])
    assert meta.is_replica
    assert meta.replica_source_host == "10.0.0.9"


def test_falls_back_to_legacy_master_host_key() -> None:
    meta = _collect([("SHOW REPLICA STATUS", [{"Master_Host": "10.0.0.8"}])])
    assert meta.replica_source_host == "10.0.0.8"


def test_counts_downstream_replicas() -> None:
    meta = _collect([("SHOW REPLICAS", [{"Host": "r1"}, {"Host": "r2"}])])
    assert meta.connected_replica_count == 2
    assert meta.connected_replica_hosts == ["r1", "r2"]


def test_missing_replication_client_grant_degrades() -> None:
    meta = _collect([("SHOW REPLICA STATUS", RAISES_ERROR), ("SHOW REPLICAS", RAISES_ERROR)])
    assert not meta.is_replica
    assert meta.connected_replica_count == 0


def test_numeric_variables_are_typed() -> None:
    meta = _collect()
    assert meta.lower_case_table_names == 0
    assert meta.max_connections == 4000


def test_absent_variable_is_none_not_zero() -> None:
    meta = _collect([("@@max_connections", RAISES_ERROR)])
    assert meta.max_connections is None
