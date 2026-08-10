from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tishift_cloudsql.config import GcpConfig, SourceConfig, load_config


def test_loads_all_sections(sample_config_path: Path) -> None:
    cfg = load_config(sample_config_path)
    assert cfg.source.host == "127.0.0.1"
    assert cfg.source.connection_method == "auth_proxy"
    assert cfg.target.tier == "essential"
    assert cfg.gcp.instance == "demo-instance"
    assert cfg.output.formats == ["cli", "json"]


def test_resolves_env_vars(sample_config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TISHIFT_TEST_SOURCE_PASSWORD", "s3cret")
    cfg = load_config(sample_config_path)
    assert cfg.source.password == "s3cret"


def test_unset_env_var_is_left_verbatim(sample_config_path: Path) -> None:
    # Left as-is on purpose, so the resulting connection error names the
    # placeholder the operator forgot to export.
    cfg = load_config(sample_config_path)
    assert cfg.source.password == "${TISHIFT_TEST_SOURCE_PASSWORD}"


def test_resolves_embedded_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGION", "us-central1")
    path = tmp_path / "c.yaml"
    path.write_text(
        "source: {host: h, user: u, database: d}\n"
        "target: {host: t, user: u, database: d}\n"
        "gcp: {region: 'prefix-${REGION}'}\n"
    )
    assert load_config(path).gcp.region == "prefix-us-central1"


def test_rejects_unknown_tier(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "source: {host: h, user: u, database: d}\n"
        "target: {host: t, user: u, database: d, tier: platinum}\n"
    )
    with pytest.raises(ValidationError, match="tier must be one of"):
        load_config(path)


def test_rejects_unknown_connection_method() -> None:
    with pytest.raises(ValidationError, match="connection_method must be one of"):
        SourceConfig(host="h", user="u", database="d", connection_method="carrier_pigeon")


def test_normalizes_connection_method_spelling() -> None:
    cfg = SourceConfig(host="h", user="u", database="d", connection_method="Public-IP")
    assert cfg.connection_method == "public_ip"


def test_public_ip_requires_tls() -> None:
    # Credentials and the whole schema dump would cross the public internet.
    with pytest.raises(ValidationError, match="source.tls must be true"):
        SourceConfig(
            host="34.1.2.3", user="u", database="d", connection_method="public_ip", tls=False
        )


def test_auth_proxy_may_disable_tls() -> None:
    # The proxy terminates TLS itself; the loopback hop is plaintext by design.
    cfg = SourceConfig(
        host="127.0.0.1", user="u", database="d", connection_method="auth_proxy", tls=False
    )
    assert cfg.tls is False


def test_export_bucket_must_be_gs_uri() -> None:
    with pytest.raises(ValidationError, match="must be a gs:// URI"):
        GcpConfig(export_bucket="my-bucket")


def test_export_bucket_trailing_slash_stripped() -> None:
    assert GcpConfig(export_bucket="gs://b/").export_bucket == "gs://b"
