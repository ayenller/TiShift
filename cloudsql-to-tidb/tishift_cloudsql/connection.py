"""Connection management for Cloud SQL for MySQL (source) and TiDB (target).

Both endpoints speak the MySQL protocol, so pymysql is used for both sides.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymysql

from tishift_cloudsql.config import SourceConfig, TargetConfig

logger = logging.getLogger(__name__)


def connect_source(config: SourceConfig, read_only: bool = True) -> pymysql.Connection:
    """Connect to a Cloud SQL for MySQL instance.

    Three network paths are supported (config.connection_method):

    * ``auth_proxy`` — the Cloud SQL Auth Proxy running locally, which handles
      IAM authentication and TLS itself and exposes a plaintext loopback socket.
      Set ``tls: false``; encrypting the loopback hop again buys nothing.
    * ``public_ip`` — direct to the instance's public IP, which requires the
      client address to be in the instance's authorized networks. TLS is
      required (enforced in config.py).
    * ``private_ip`` — direct over VPC peering or Private Service Connect.

    Enforces read-only at the session level so scan can never mutate the source.
    """
    import pymysql

    ssl = {"ssl": {"ca": config.ssl_ca}} if config.tls else {}

    conn = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        connect_timeout=15,
        cursorclass=pymysql.cursors.DictCursor,
        **ssl,
    )

    if read_only:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
        logger.debug("Source connection set to read-only.")

    return conn


def connect_target(config: TargetConfig) -> pymysql.Connection:
    """Connect to a TiDB target database."""
    import pymysql

    ssl = {"ssl": {"ca": config.ssl_ca}} if config.tls else {}

    conn = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
        **ssl,
    )
    return conn
