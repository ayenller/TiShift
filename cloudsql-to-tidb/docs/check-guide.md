# Check guide

**Status: not implemented.** `tishift-cloudsql check` exits 2. Run these four
checks manually, in order — each one is cheap and rules out a different class of
failure.

## 1. Row counts

```sql
SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'myapp' AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME;
```

`TABLE_ROWS` is an estimate on both sides. Use it to spot tables that are
obviously empty or obviously short; confirm suspects with `COUNT(*)`.

## 2. Structure diff

```sql
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'myapp' ORDER BY TABLE_NAME, ORDINAL_POSITION;
```

Run on both, diff the output.

**Benign differences you will always see** — verified on a real 57-table
migration, both are information_schema reporting quirks, not semantic drift:

| Column | Cloud SQL 8.4 | TiDB 8.5 | Why |
|---|---|---|---|
| `EXTRA` on a column with `DEFAULT CURRENT_TIMESTAMP` | `DEFAULT_GENERATED` | empty | MySQL 8 tags expression defaults; TiDB does not. The default itself behaves identically |
| `COLUMN_TYPE` of a `YEAR` column | `year` | `year(4)` | TiDB still reports the display width MySQL 8.0 dropped. Same type, same range |

Filter both out before treating a diff as a finding.

Expect and verify the deliberate differences:
`utf8`→`utf8mb4` (CSQL-DDL-4), `MyISAM`→`InnoDB` (CSQL-DDL-2), missing spatial
indexes (CSQL-DDL-6). Anything else is a defect.

## 3. Checksums

```sql
SELECT BIT_XOR(CRC32(CONCAT_WS('#', col1, col2, col3))) AS checksum
FROM orders WHERE id BETWEEN 1 AND 1000000;
```

Order-independent, so it works regardless of how rows were loaded. Notes:

- `CONCAT_WS` skips NULLs, so `('a', NULL, 'b')` and `('a', 'b')` collide. Add a
  NULL-distinguishing column or use `COALESCE` with a sentinel if that matters.
- Compare over the same PK ranges on both sides, not whole tables, so a mismatch
  points at a range you can investigate.
- Floating-point columns will not match reliably; exclude them.

## 3b. FULLTEXT indexes report as BTREE on TiDB

Do not use `information_schema.STATISTICS.INDEX_TYPE` to confirm a FULLTEXT
index survived. On TiDB v8.5.3 a real, working FULLTEXT index is reported there
as `BTREE`, so a structure diff against the Cloud SQL source will show a
spurious difference. `SHOW CREATE TABLE` is the source of truth:

```sql
SHOW CREATE TABLE `0_stock_category`\G
-- FULLTEXT INDEX `..._fdx`(`description`) WITH PARSER STANDARD
```

(Verified: TiDB Cloud Starter does build real FULLTEXT indexes, which is what
WARNING-2 assumes when it stays silent on that tier.)

## 4. TiFlash replicas

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, REPLICA_COUNT, AVAILABLE, PROGRESS
FROM information_schema.tiflash_replica WHERE TABLE_SCHEMA = 'myapp';
```

`AVAILABLE = 1` on every table the convert phase emitted an `ALTER` for.
`PROGRESS < 1` means replication is still catching up — not an error, just not
done.

## Also worth checking

- **Users and grants.** IAM database users (CSQL-WARNING-1) do not exist on
  TiDB. Every affected client needs new credentials before cutover.
- **`sql_mode`.** If the source was laxer (CSQL-WARNING-4), rows that inserted
  cleanly there may have been rejected here. A silent row-count shortfall on one
  table is the usual symptom.
- **Views and routines.** Views apply without their `DEFINER`; stored procedures
  and triggers do not come across at all (BLOCKER-1/2). Confirm the application
  no longer calls them.

## When this is implemented

The exit-code contract will be: 0 all checks passed, 1 a check failed, 2 could
not run. Until then it exits 2 unconditionally.
