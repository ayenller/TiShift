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


def tls_kwargs(tls: bool, ssl_ca: str) -> dict:
    """Build pymysql's TLS keyword arguments.

    Three distinct cases, and the middle one is the trap:

    * ``tls=False`` -> ``ssl_disabled=True``. Passing no ssl argument at all is
      NOT the same thing: pymysql negotiates TLS opportunistically, so a config
      that says "no TLS" would silently get it.
    * ``tls=True`` with a CA -> verify the server certificate against it.
    * ``tls=True`` without a CA -> ``ssl={}``: encrypted, but the certificate is
      not verified. It cannot be ``{"ca": ""}`` — that makes pymysql demand
      verification with nothing to verify against and every connection dies with
      CERTIFICATE_VERIFY_FAILED.

    Falling back to the system CA bundle is not an option worth offering here:
    Cloud SQL signs each instance's server certificate with a *per-instance*
    self-signed CA, so system roots can never validate it. Get the real CA with
    ``gcloud sql ssl server-ca-certs list --instance=<INSTANCE>`` and point
    ``source.ssl_ca`` at it. Callers should tell the user when verification is
    off — see `tls_is_unverified`.
    """
    if not tls:
        return {"ssl_disabled": True}
    if ssl_ca:
        return {"ssl": {"ca": ssl_ca}}
    return {"ssl": {}}


def tls_is_unverified(config: SourceConfig) -> bool:
    """True when the connection will be encrypted but the server unauthenticated."""
    return config.tls and not config.ssl_ca


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

    if tls_is_unverified(config):
        # Deliberately debug, not warning: Python's last-resort handler would
        # print a warning to stderr on top of the CLI's own message. Callers
        # surface this via `tls_is_unverified`, which the CLI does.
        logger.debug("TLS on, ssl_ca unset — encrypted but the server is not verified.")

    conn = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        connect_timeout=15,
        cursorclass=pymysql.cursors.DictCursor,
        **tls_kwargs(config.tls, config.ssl_ca),
    )

    if read_only:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
        logger.debug("Source connection set to read-only.")

    return conn


def connect_target(config: TargetConfig) -> pymysql.Connection:
    """Connect to a TiDB target database."""
    import pymysql

    conn = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
        **tls_kwargs(config.tls, config.ssl_ca),
    )
    return conn
