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

Run on both, diff the output. Expect and verify the deliberate differences:
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
