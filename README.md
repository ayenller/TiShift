# TiShift

AI-assisted database migration toolkit for [TiDB Cloud](https://www.pingcap.com/tidb-cloud/).

TiShift automates the heavy lifting of migrating from legacy databases to TiDB Cloud — scanning source schemas, assessing compatibility, converting DDL, loading data, and validating results.

## Supported Migration Paths

| Source | Target | Status |
|---|---|---|
| **OceanBase** | TiDB Cloud (Starter, Essential, Dedicated) | Active |
| **CockroachDB** | TiDB Cloud (Starter, Essential, Dedicated) | Active |
| **Cloud Spanner** | TiDB Cloud (Starter, Essential, Dedicated) | Active |
| **Neon / PostgreSQL** | TiDB Cloud (Starter, Essential, Dedicated) | Active |
| **Supabase** | TiDB Cloud (Starter, Essential, Dedicated) | Active |
| **Oracle Database** | TiDB Cloud (Starter, Essential, Dedicated) | Active |
| **SQL Server / MSSQL** | TiDB Cloud (Starter, Essential, Dedicated) | Active |
| **Aurora MySQL** | TiDB Cloud (Starter, Essential, Dedicated) | Active |
| **Cloud Firestore** | TiDB Cloud (Starter, Essential, Dedicated, BYOC on GCP) | Active |
| **MySQL HeatWave** | TiDB Cloud (Starter, Essential, Dedicated) | Active |
| **Cloud SQL for MySQL** | TiDB Cloud (Starter, Essential, Dedicated) | Active |

## How It Works

TiShift follows a phased migration workflow:

```
 ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
 │  1.Scan  │───▶│ 2.Assess │───▶│3.Convert │───▶│ 4. Load  │───▶│5.Validate│
 │          │    │ & Score  │    │  Schema  │    │   Data   │    │          │
 └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
```

1. **Scan** — Collect schema inventory, data profile, and feature usage from the source database
2. **Assess & Score** — Identify blockers, warnings, and compute a 0-100 readiness score with per-category breakdowns
3. **Convert** — Generate TiDB-compatible DDL, rewrite queries, and produce code stubs for stored procedures/triggers
4. **Load** — Transfer data using the optimal strategy for your tier (ticloud import, direct load, DMS, or Lightning)
5. **Validate** — Compare row counts, column structures, and data integrity between source and target

## Getting Started

TiShift ships as a set of AI coding assistant skills that guide you interactively through every migration phase. Each skill connects to your source and target databases, runs the scan, scores readiness, converts schemas, loads data, and validates results — step by step.

### Prerequisites

- An AI coding assistant that supports skills / slash commands
- Network access to your source database (SQL Server or Aurora MySQL)
- A TiDB Cloud cluster (a free [Starter](https://tidbcloud.com/) tier works)

### 1. Clone and open the project

```bash
git clone https://github.com/pingcap-inc/TiShift.git
cd TiShift
# Open the project in your AI coding assistant
```

### 2. Run the skill for your source database

#### OceanBase to TiDB Cloud

```
/oceanbase-to-tidb
```

#### CockroachDB to TiDB Cloud

```
/cockroachdb-to-tidb
```

#### Cloud Spanner to TiDB Cloud

```
/spanner-to-tidb
```

#### Neon / PostgreSQL to TiDB Cloud

```
/neon-to-tidb
```

#### Supabase to TiDB Cloud

```
/supabase-to-tidb
```

#### Aurora MySQL to TiDB Cloud

```
/aurora-to-tidb
```

#### Cloud Firestore to TiDB Cloud

```
/firestore-to-tidb
```

#### SQL Server to TiDB Cloud

```
/sqlserver-to-tidb
```

#### Oracle to TiDB Cloud

```
/oracle-to-tidb
```

#### MySQL HeatWave to TiDB Cloud

```
/heatwave-to-tidb
```

#### Cloud SQL for MySQL to TiDB Cloud

```
/cloudsql-to-tidb
```

The skill will walk you through each phase — connecting to your databases, scanning the source schema, assessing compatibility, converting DDL, loading data, and validating the result. Follow the prompts; no additional setup is required.

## TiDB Cloud Tier Awareness

TiShift is Cloud-first and defaults to **TiDB Cloud Starter** (free tier). It automatically adjusts recommendations based on your target tier:

| Feature | Starter | Essential | Dedicated |
|---|---|---|---|
| Import method | `ticloud serverless import start` | Direct / DMS | Direct / DMS / Lightning |
| CDC sync | Not available (cutover only) | Changefeeds | Changefeeds / DM |
| Storage | 25 GiB free | Auto-scaled | Configurable |
| Cost | Free to start | ~$20/day | From $1,376/mo |

## Optional: CLI Toolkit

For environments where AI-assisted migration is not available, TiShift also provides deterministic Python CLI scripts that cover the same workflow.

### OceanBase to TiDB Cloud

```bash
cd oceanbase-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp config/tishift-ob.example.yaml tishift-ob.yaml
# Edit with OceanBase credentials (port 2881, user@tenant)

tishift-ob scan --config tishift-ob.yaml --format cli --format json
tishift-ob convert --scan-report ./tishift-reports/report.json --dry-run
tishift-ob load --config tishift-ob.yaml --strategy auto
tishift-ob check --config tishift-ob.yaml
```

### CockroachDB to TiDB Cloud

```bash
cd cockroachdb-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp config/tishift-crdb.example.yaml tishift-crdb.yaml
# Edit with your CockroachDB and TiDB credentials

# Scan and assess
tishift-crdb scan --config tishift-crdb.yaml --format cli --format json

# Convert schema
tishift-crdb convert --scan-report ./tishift-reports/report.json --dry-run

# Load data (uses EXPORT INTO CSV from CockroachDB)
tishift-crdb load --config tishift-crdb.yaml --strategy auto

# Validate
tishift-crdb check --config tishift-crdb.yaml
```

### Cloud Spanner to TiDB Cloud

```bash
cd spanner-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp config/tishift-spanner.example.yaml tishift-spanner.yaml
# Set GOOGLE_APPLICATION_CREDENTIALS or run gcloud auth application-default login

# Scan and assess
tishift-spanner scan --config tishift-spanner.yaml --format cli --format json

# Convert schema
tishift-spanner convert --scan-report ./tishift-reports/tishift-spanner-report.json --dry-run

# Load data (requires GCS bucket for Dataflow export)
tishift-spanner load --config tishift-spanner.yaml --strategy auto

# Validate
tishift-spanner check --config tishift-spanner.yaml
```

### Neon / PostgreSQL to TiDB Cloud

```bash
cd neon-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp config/tishift-neon.example.yaml tishift-neon.yaml
# Edit tishift-neon.yaml with your source and target credentials

# Scan and assess
tishift-neon scan --config tishift-neon.yaml --format cli --format json

# Convert schema
tishift-neon convert --scan-report ./tishift-reports/tishift-neon-report.json --dry-run

# Load data
tishift-neon load --config tishift-neon.yaml --strategy auto

# Validate
tishift-neon check --config tishift-neon.yaml
```

### Supabase to TiDB Cloud

```bash
cd supabase-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp config/tishift-supabase.example.yaml tishift-supabase.yaml
# Edit tishift-supabase.yaml with your Supabase and TiDB credentials.
# Use the direct endpoint (db.{ref}.supabase.co:5432) or the session-mode pooler
# (port 5432 on *.pooler.supabase.com). The transaction-mode pooler (port 6543)
# is refused — it breaks pg_dump, prepared statements, and replication.

# Scan and assess — extracts every RLS policy as a structured rewrite checklist
tishift-supabase scan --config tishift-supabase.yaml --format cli --format json

# Convert schema (also emits the external-work plan for PostgREST / GoTrue /
# Realtime / Storage replacements that live outside the DB)
tishift-supabase convert --scan-report ./tishift-reports/tishift-supabase-report.json --dry-run

# Load data (schema allow-list mandatory — public + user schemas only)
tishift-supabase load --config tishift-supabase.yaml --strategy auto

# Validate
tishift-supabase check --config tishift-supabase.yaml
```

### SQL Server to TiDB Cloud

```bash
cd sqlserver-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp config/tishift-mssql.example.yaml tishift-mssql.yaml
# Edit tishift-mssql.yaml with your source and target credentials

# Scan and assess
tishift-mssql scan --config tishift-mssql.yaml --format cli --format json --cost

# Convert schema
tishift-mssql convert --scan-report ./tishift-reports/tishift-mssql-report.json --dry-run

# Load data
tishift-mssql load --config tishift-mssql.yaml --strategy auto

# Validate
tishift-mssql check --config tishift-mssql.yaml
```

### Oracle to TiDB Cloud

```bash
cd oracle-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp config/tishift-oracle.example.yaml tishift-oracle.yaml
# Edit tishift-oracle.yaml with your source and target credentials

# Scan and assess
tishift-oracle scan --config tishift-oracle.yaml --format cli --format json

# Convert schema
tishift-oracle convert --scan-report ./tishift-reports/report.json --dry-run

# Load data
tishift-oracle load --config tishift-oracle.yaml --strategy auto

# Validate
tishift-oracle check --config tishift-oracle.yaml
```

### Aurora MySQL to TiDB Cloud

```bash
cd aurora-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Scan and assess
python -m tishift.cli scan --config tishift.yaml

# Convert schema
python -m tishift.cli convert --config tishift.yaml --scan-report ./tishift-reports/tishift-report.json

# Load and validate
python -m tishift.cli load --config tishift.yaml --scan-report ./tishift-reports/tishift-report.json
python -m tishift.cli check --config tishift.yaml --schema mydb
```

### MySQL HeatWave to TiDB Cloud

```bash
cd heatwave-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp config/tishift-heatwave.example.yaml tishift-heatwave.yaml
# Edit with your HeatWave and TiDB credentials.
# HeatWave DB Systems are VCN-private — connect through an SSH tunnel or
# OCI Bastion session (ssh -L 3306:<db-private-ip>:3306 opc@<bastion>).

# Scan and assess — detects RAPID offload, Lakehouse tables, AutoML schemas
tishift-heatwave scan --config tishift-heatwave.yaml --format cli --format json

# Convert schema (comments out SECONDARY_ENGINE/CLUSTERING BY clauses as
# TISHIFT-REMOVED markers, inlines TiFlash replica DDL after each RAPID table)
tishift-heatwave convert --ddl-file schema.sql --tier dedicated --dry-run

# Load and check are not automated yet — both commands print a pointer to
# the manual runbook and exit non-zero; follow docs/load-guide.md and
# docs/check-guide.md (or the AI skill) by hand until they land.
tishift-heatwave load --config tishift-heatwave.yaml --strategy auto
tishift-heatwave check --config tishift-heatwave.yaml
```

### Cloud SQL for MySQL to TiDB Cloud

```bash
cd cloudsql-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp config/tishift-cloudsql.example.yaml tishift-cloudsql.yaml
# Edit with your Cloud SQL and TiDB credentials, then start the Auth Proxy:
#   cloud-sql-proxy --port 3306 PROJECT:REGION:INSTANCE
# Note: the Auth Proxy is a local connector — TiDB Cloud DM cannot use it.
# Plan a public or private IP path if you need continue replication.

# Scan and assess — covers the Cloud SQL platform surface (IAM database users,
# DEFINER principals, mysql.heartbeat, replication topology, binlog settings)
tishift-cloudsql scan --config tishift-cloudsql.yaml --format cli --format json

# Add DM readiness checks (binlog rules + valid-index precheck) to the score
tishift-cloudsql scan --config tishift-cloudsql.yaml --continue-replication

# Convert schema (strips DEFINER clauses, rewrites non-InnoDB engines and
# utf8/utf8mb3 charsets, inlines TiFlash replica DDL after FULLTEXT tables)
tishift-cloudsql convert --ddl-file source-schema.sql --dry-run

# Load is intentionally disabled; check and sync are documented but not
# automated. All three exit non-zero and point at their runbook.
tishift-cloudsql load --config tishift-cloudsql.yaml
tishift-cloudsql check --config tishift-cloudsql.yaml
```

### Cloud Firestore to TiDB Cloud

```bash
cd firestore-to-tidb
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp config/tishift-firestore.example.yaml tishift-firestore.yaml
# Edit with your GCP project, Firestore database, GCS staging bucket, and TiDB credentials.
# Firestore Native mode is required. Datastore mode supported at limited fidelity.
# Firestore Enterprise with MongoDB-compatibility API is routed to the future mongo-to-tidb skill.

# Verify connectivity and IAM
tishift-firestore preflight --config tishift-firestore.yaml

# Scan and assess — samples documents, infers schema, enumerates composite indexes
tishift-firestore scan --config tishift-firestore.yaml --format cli --format json
tishift-firestore score --config tishift-firestore.yaml \
    --scan-report tishift-reports/firestore-scan-report.json

# Convert schema (composite-index-driven JSON / hybrid / normalized policy)
tishift-firestore convert --config tishift-firestore.yaml \
    --scan-report tishift-reports/firestore-scan-report.json --dry-run
tishift-firestore convert --config tishift-firestore.yaml \
    --scan-report tishift-reports/firestore-scan-report.json --apply

# Load data (Dataflow + Beam Firestore IO → GCS NDJSON → TiDB Lightning for BYOC)
tishift-firestore load --config tishift-firestore.yaml \
    --scan-report tishift-reports/firestore-scan-report.json --strategy auto

# Validate
tishift-firestore check --config tishift-firestore.yaml \
    --scan-report tishift-reports/firestore-scan-report.json
```

### Configuration

The CLI uses YAML config files with environment variable support for credentials:

```yaml
source:
  host: sqlserver.example.com
  user: sa
  password: ${TISHIFT_SOURCE_PASSWORD}
  database: myapp

target:
  host: gateway01.us-east-1.prod.aws.tidbcloud.com
  port: 4000
  user: root
  password: ${TISHIFT_TARGET_PASSWORD}
  database: myapp
  tls: true
  tier: starter    # starter | essential | dedicated | self-hosted
```

See `config/tishift-mssql.example.yaml` for the full configuration reference.

### Tests

```bash
# Spanner toolkit
cd spanner-to-tidb && pytest tests -q

# Neon toolkit
cd neon-to-tidb && pytest tests -q

# Supabase toolkit
cd supabase-to-tidb && pytest tests -q

# SQL Server toolkit
cd sqlserver-to-tidb && pytest tests -q

# Aurora toolkit
cd aurora-to-tidb && pytest tests -q

# Firestore toolkit
cd firestore-to-tidb && pytest tests -q

# HeatWave toolkit
cd heatwave-to-tidb && pytest tests -q

# Cloud SQL toolkit
cd cloudsql-to-tidb && pytest tests -q
```

## Project Structure

```
TiShift/
├── oceanbase-to-tidb/          OceanBase → TiDB Cloud migration
│   ├── SKILL.md                AI skill (dual-mode: MySQL + Oracle)
│   ├── references/             Type mappings (both modes), compatibility rules, scoring
│   ├── tishift_ob/             Python CLI toolkit
│   └── tests/
│
├── cockroachdb-to-tidb/        CockroachDB → TiDB Cloud migration
│   ├── SKILL.md                AI skill (interactive migration guide)
│   ├── references/             Type mappings, compatibility rules, scoring
│   ├── tishift_crdb/           Python CLI toolkit
│   └── tests/
│
├── spanner-to-tidb/            Cloud Spanner → TiDB Cloud migration
│   ├── SKILL.md                AI skill (interactive migration guide)
│   ├── references/             Type mappings, compatibility rules, scoring
│   ├── tishift_spanner/        Python CLI toolkit
│   │   ├── core/scan/          Schema collectors, analyzers, reporters
│   │   ├── core/convert/       DDL generation, interleave flattening
│   │   ├── core/load/          Dataflow export, GCS download, Lightning
│   │   ├── core/check/         Row count, column, checksum validation
│   │   ├── core/sync/          CDC via Change Streams
│   │   └── rules/              Type mapping, compatibility, GoogleSQL patterns
│   └── tests/                  Unit and integration tests
│
├── oracle-to-tidb/             Oracle → TiDB Cloud migration
│   ├── SKILL.md                AI skill (interactive migration guide)
│   ├── references/             Type mappings, compatibility rules, scoring
│   ├── tishift_oracle/         Python CLI toolkit
│   │   ├── core/scan/          Schema collectors, analyzers, reporters
│   │   ├── core/convert/       DDL generation, PL/SQL rewriting, code stubs
│   │   ├── core/load/          CSV extraction, DMS, Lightning, ticloud import
│   │   ├── core/check/         Row count, column, NULL semantics validation
│   │   ├── core/sync/          CDC via DMS, Debezium
│   │   └── rules/              Type mapping, compatibility, Oracle patterns
│   └── tests/                  Unit and integration tests
│
├── neon-to-tidb/               Neon/Postgres → TiDB Cloud migration
│   ├── SKILL.md                AI skill (interactive migration guide)
│   ├── references/             Type mappings, compatibility rules, scoring
│   ├── tishift_neon/           Python CLI toolkit
│   │   ├── core/scan/          Schema collectors, analyzers, reporters
│   │   ├── core/convert/       DDL generation, query rewriting, code stubs
│   │   ├── core/load/          Direct, DMS, Lightning, ticloud import
│   │   ├── core/check/         Row count, column, checksum validation
│   │   ├── core/sync/          CDC via logical replication
│   │   └── rules/              Type mapping, compatibility, PG patterns
│   └── tests/                  Unit and integration tests
│
├── supabase-to-tidb/           Supabase → TiDB Cloud migration
│   ├── SKILL.md                AI skill (interactive migration guide)
│   ├── references/             Type mappings, compatibility rules, scoring
│   ├── docs/                   Getting started + phase-by-phase guides
│   ├── tishift_supabase/       Python CLI toolkit
│   │   ├── core/scan/          Schema + RLS + platform-signals collectors
│   │   ├── core/convert/       DDL, RLS rewrite checklist, external-work plan
│   │   ├── core/load/          Direct, DMS, Lightning, ticloud (schema allow-list)
│   │   ├── core/check/         Row count, column, checksum validation
│   │   ├── core/sync/          CDC via logical replication (direct endpoint only)
│   │   └── rules/              Type mapping, compatibility, Supabase helpers
│   └── tests/                  Unit and integration tests
│
├── sqlserver-to-tidb/          SQL Server → TiDB Cloud migration
│   ├── SKILL.md                AI skill (interactive migration guide)
│   ├── references/             Type mappings, compatibility rules, scoring
│   ├── tishift_mssql/          Python CLI toolkit
│   │   ├── scan/               Schema collectors, analyzers, reporters
│   │   ├── convert/            DDL generation, query rewriting, code stubs
│   │   ├── load/               Direct, DMS, Lightning, ticloud import
│   │   ├── check/              Row count, column, checksum validation
│   │   ├── sync/               CDC via DM, DMS, Changefeeds
│   │   └── rules/              Type mapping, compatibility, T-SQL patterns
│   └── tests/                  Unit and integration tests
│
├── aurora-to-tidb/             Aurora MySQL → TiDB Cloud migration
│   ├── SKILL.md                AI skill (interactive migration guide)
│   ├── references/             Compatibility rules, load strategies, scoring
│   ├── tishift/                Python CLI toolkit
│   │   ├── scan/               Schema collectors, analyzers, reporters
│   │   ├── convert/            DDL generation, query rewriting, code stubs
│   │   ├── load/               Direct, DMS, Cloud Import, Lightning
│   │   ├── check/              Row count, column, checksum validation
│   │   ├── sync/               CDC via DM, DMS, Changefeeds
│   │   └── rules/              Compatibility rules, type mapping
│   └── tests/                  Unit and integration tests
│
├── firestore-to-tidb/          Cloud Firestore → TiDB Cloud migration
│   ├── SKILL.md                AI skill (interactive migration guide)
│   ├── references/             Type mappings, compatibility rules, scoring,
│   │                           schema-policy engine, BYOC runbook
│   ├── tishift_firestore/      Python CLI + MCP toolkit
│   │   ├── core/scan/          Sample-based inference, index enumeration
│   │   ├── core/convert/       Schema policy engine, template DDL emission
│   │   ├── core/load/          Apache Beam on Dataflow → GCS NDJSON → Lightning
│   │   ├── core/check/         Counts, structure, canonical hash diff
│   │   ├── core/sync/          BigQuery bridge via firestore-bigquery-export
│   │   └── rules/              Type mapping, compatibility rules, scoring
│   └── tests/                  Unit and integration tests (Firestore Emulator)
│
├── heatwave-to-tidb/           MySQL HeatWave → TiDB Cloud migration
│   ├── SKILL.md                AI skill (interactive migration guide)
│   ├── references/             Type mappings, compatibility rules, load
│   │                           strategies (RAPID → TiFlash mapping), scoring
│   ├── tishift_heatwave/       Python CLI toolkit
│   │   ├── core/scan/          Schema collectors, HeatWave/RAPID/Lakehouse detection
│   │   ├── core/convert/       DDL transform, TiFlash emission, code stubs
│   │   ├── core/load/          Dumpling, ticloud import, direct, Lightning
│   │   ├── core/check/         Row count, column, checksum validation
│   │   ├── core/sync/          Continue replication via TiDB DM (binlog replication)
│   │   └── rules/              Type mapping, compatibility rules, scoring
│   └── tests/                  Unit tests
│
├── cloudsql-to-tidb/           Cloud SQL for MySQL → TiDB Cloud migration
│   ├── SKILL.md                AI skill (interactive migration guide)
│   ├── references/             Compatibility rules, type mappings, GCS load
│   │                           strategies, scoring
│   ├── docs/                   Per-phase operator guides + flat checklist
│   ├── tishift_cloudsql/       Python CLI toolkit
│   │   ├── gcloud.py           Renders the gcloud commands quoted in reports
│   │   ├── core/scan/          Platform/IAM/topology collectors, schema inventory
│   │   ├── core/convert/       CSQL-DDL transforms, TiFlash emission
│   │   ├── core/load/          Intentionally disabled (see docs/load-guide.md)
│   │   ├── core/check/         Documented, not yet automated
│   │   ├── core/sync/          DM prechecks — documented, not yet automated
│   │   └── rules/              Compatibility, binlog, DDL cleanup, scoring
│   └── tests/                  Unit tests (offline, no database required)
│
└── LICENSE                     Apache 2.0
```

## License

[Apache 2.0](LICENSE)
