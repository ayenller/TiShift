"""Configuration loading and validation for TiShift Cloud SQL."""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

VALID_TIERS = ("starter", "essential", "dedicated", "self-hosted")

# How the toolkit reaches the Cloud SQL instance. This is not cosmetic: the
# Auth Proxy is a *local* client-side connector, so a source that `scan` reaches
# happily through it can still be completely unreachable by TiDB Cloud DM, which
# is a managed service and needs direct TCP access. See docs/sync-guide.md.
VALID_CONNECTION_METHODS = ("auth_proxy", "public_ip", "private_ip")


class SourceConfig(BaseModel):
    host: str
    port: int = 3306
    user: str
    password: str = ""
    database: str
    # The Auth Proxy terminates TLS itself and exposes a plaintext loopback
    # socket, so `tls: false` is the correct setting for connection_method
    # auth_proxy — it is not a downgrade.
    tls: bool = True
    # Instance server CA, downloaded from the Cloud SQL console or via
    # `gcloud sql ssl server-ca-certs list`. Only needed for direct IP
    # connections; empty means system CAs.
    ssl_ca: str = ""
    connection_method: str = "auth_proxy"

    @field_validator("connection_method")
    @classmethod
    def _normalize_connection_method(cls, value: str) -> str:
        method = value.strip().lower().replace("-", "_")
        if method not in VALID_CONNECTION_METHODS:
            raise ValueError(
                f"connection_method must be one of {', '.join(VALID_CONNECTION_METHODS)}; "
                f"got {value!r}"
            )
        return method

    @model_validator(mode="after")
    def _require_tls_on_public_ip(self) -> SourceConfig:
        # Refused rather than warned about: public_ip means the credentials and
        # the entire schema dump cross the public internet, and a scan that
        # "worked" is exactly when nobody goes back to fix it.
        if self.connection_method == "public_ip" and not self.tls:
            raise ValueError(
                "source.tls must be true when connection_method is public_ip — "
                "use connection_method auth_proxy for an unencrypted loopback connection"
            )
        return self


class GcpConfig(BaseModel):
    """Instance coordinates used to render exact gcloud commands in reports.

    Every field is optional: `scan` and `convert` work fine without them, and
    fall back to `<PLACEHOLDER>` text in remediation output. Filling them in is
    what turns "set binlog_row_image to FULL" into a command the operator can
    paste. See gcloud.py for the rendering.
    """

    project: str = ""
    instance: str = ""
    region: str = ""
    export_bucket: str = ""

    @field_validator("export_bucket")
    @classmethod
    def _normalize_bucket(cls, value: str) -> str:
        bucket = value.strip().rstrip("/")
        if bucket and not bucket.startswith("gs://"):
            raise ValueError(f"gcp.export_bucket must be a gs:// URI; got {value!r}")
        return bucket


class TargetConfig(BaseModel):
    host: str
    port: int = 4000
    user: str
    password: str = ""
    database: str
    tls: bool = True
    ssl_ca: str = ""
    tier: str = "starter"

    @field_validator("tier")
    @classmethod
    def _normalize_tier(cls, value: str) -> str:
        tier = value.strip().lower()
        if tier not in VALID_TIERS:
            raise ValueError(f"tier must be one of {', '.join(VALID_TIERS)}; got {value!r}")
        return tier


class OutputConfig(BaseModel):
    dir: str = "./tishift-reports"
    formats: list[str] = Field(default_factory=lambda: ["cli", "json"])


class LoggingConfig(BaseModel):
    level: str = "info"
    file: str = "tishift-cloudsql.log"


class MetricsConfig(BaseModel):
    enabled: bool = False
    port: int = 9090


class TiShiftCloudSQLConfig(BaseModel):
    source: SourceConfig
    target: TargetConfig
    gcp: GcpConfig = Field(default_factory=GcpConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)


_ENV_REF = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: str) -> str:
    """Resolve ${ENV_VAR} references in a string, including embedded ones
    (e.g. ``prefix-${REGION}``). Unset variables are left as-is so the
    resulting connection error names the missing placeholder."""
    return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def load_config(path: Path) -> TiShiftCloudSQLConfig:
    """Load and validate configuration from a YAML file."""
    import yaml

    with path.open() as f:
        raw = yaml.safe_load(f)

    # Resolve environment variables in string values
    def resolve(
        obj: dict | list | str | int | float | bool | None,
    ) -> dict | list | str | int | float | bool | None:
        if isinstance(obj, dict):
            return {k: resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [resolve(v) for v in obj]
        if isinstance(obj, str):
            return _resolve_env_vars(obj)
        return obj

    resolved = resolve(raw)
    return TiShiftCloudSQLConfig(**resolved)
