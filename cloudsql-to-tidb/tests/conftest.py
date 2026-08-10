from __future__ import annotations

from pathlib import Path

import pytest

SAMPLE_CONFIG = """
source:
  connection_method: auth_proxy
  host: 127.0.0.1
  port: 3306
  user: tishift_ro
  password: ${TISHIFT_TEST_SOURCE_PASSWORD}
  database: myapp
  tls: false

gcp:
  project: demo-project
  instance: demo-instance
  region: us-central1
  export_bucket: gs://demo-bucket

target:
  host: gateway.tidbcloud.com
  port: 4000
  user: root
  password: secret
  database: myapp
  tier: essential

output:
  dir: ./reports
  formats: [cli, json]
"""


@pytest.fixture()
def sample_config_path(tmp_path: Path) -> Path:
    path = tmp_path / "tishift-cloudsql.yaml"
    path.write_text(SAMPLE_CONFIG)
    return path
