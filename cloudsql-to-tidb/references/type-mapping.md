# Type and table-option mapping

Cloud SQL for MySQL *is* MySQL, so the overwhelming majority of this mapping is
the identity function. This file documents only the exceptions — if a type is
not listed here, it transfers unchanged.

## Identity mappings

All of these are byte-for-byte compatible with TiDB and need no rewrite:

`TINYINT` `SMALLINT` `MEDIUMINT` `INT` `BIGINT` (signed and unsigned)
`DECIMAL` `NUMERIC` `FLOAT` `DOUBLE`
`DATE` `TIME` `DATETIME` `TIMESTAMP` `YEAR`
`CHAR` `VARCHAR` `BINARY` `VARBINARY`
`TINYTEXT` `TEXT` `MEDIUMTEXT` `LONGTEXT`
`TINYBLOB` `BLOB` `MEDIUMBLOB` `LONGBLOB`
`ENUM` `SET` `JSON` `BIT` `BOOLEAN`

Generated columns (VIRTUAL and STORED) and CHECK constraints also transfer
unchanged.

## Exceptions

| Source | TiDB | Rule | Note |
|---|---|---|---|
| `GEOMETRY`, `POINT`, `LINESTRING`, `POLYGON`, `MULTIPOINT`, `MULTILINESTRING`, `MULTIPOLYGON`, `GEOMETRYCOLLECTION` | *none* | BLOCKER-4 / CSQL-DDL-6 | Convert to `JSON` with `COMMENT 'was: <type>'`. Spatial functions and `SPATIAL` indexes are unsupported too — this is an application rewrite, not a type swap |
| `CHARACTER SET utf8` / `utf8mb3` | `utf8mb4` | CSQL-DDL-4 | utf8mb3 stores at most 3 bytes per character, so it cannot hold 4-byte characters (emoji, some CJK). Widening is safe for data but changes the max column length in characters for indexed columns — a `VARCHAR(255)` index key grows from 765 to 1020 bytes |
| `utf8mb3_general_ci`, `utf8mb3_unicode_ci` | `utf8mb4_general_ci` | CSQL-DDL-4 | Sort order changes. Anything relying on collation-dependent ordering or uniqueness needs re-verification |
| `utf8mb4_0900_ai_ci` and other `utf8mb4_0900_*` | same | WARNING-4 | Supported natively since TiDB v7.4 — no rewrite |
| charsets outside {ascii, latin1, binary, utf8, utf8mb4, gbk} | *none* | BLOCKER-8 | Rejected outright by TiDB, not degraded. Convert on the source before exporting |
| `AUTO_INCREMENT` on a single-column integer PK | `AUTO_RANDOM` (suggested) | WARNING-3 / CSQL-DDL-7 | TiDB guarantees uniqueness but not monotonicity. Sequential PKs concentrate writes on one Region — `AUTO_RANDOM` spreads them. **Never applied automatically**: it changes the ID values the application sees |

## Table options

| Option | TiDB action | Rule |
|---|---|---|
| `ENGINE=InnoDB` | kept | — |
| `ENGINE=MyISAM` / `MEMORY` / `ARCHIVE` / `CSV` | rewritten to `InnoDB` | CSQL-DDL-2 |
| `ROW_FORMAT=…`, `KEY_BLOCK_SIZE=…` | commented out | CSQL-DDL-3 |
| `ENCRYPTION=…` | commented out — TiDB Cloud encrypts at rest by default | CSQL-DDL-3 |
| `COMPRESSION=…`, `TABLESPACE=…` | commented out | CSQL-DDL-3 |
| `STATS_PERSISTENT`, `STATS_AUTO_RECALC`, `STATS_SAMPLE_PAGES` | commented out — TiDB manages statistics itself | CSQL-DDL-3 |
| `AUTO_INCREMENT=<n>` | kept | — |
| `DEFAULT CHARSET` / `COLLATE` | kept, unless utf8mb3 | CSQL-DDL-4 |
| `PARTITION BY RANGE / LIST / HASH / KEY` | kept | — |
| `DEFINER=…` | commented out | CSQL-DDL-1 |

## Indexes

| Source | TiDB action | Rule |
|---|---|---|
| `PRIMARY KEY`, `UNIQUE KEY`, `KEY` (BTREE) | kept | — |
| `FULLTEXT KEY` | commented out; TiFlash replica emitted | WARNING-2 / CSQL-DDL-5 |
| `SPATIAL KEY` | commented out, flagged for review | BLOCKER-4 / CSQL-DDL-6 |
| Prefix indexes (`KEY (col(20))`) | kept | — |
| Descending indexes | kept | — |

## The MySQL 5.7 source case

Cloud SQL still runs 5.7 instances. TiDB targets 8.0 semantics, so a 5.7 source
carries extra review items (CSQL-WARNING-3):

- The default charset is `latin1` on 5.7 and `utf8mb4` on 8.0. Tables created
  without an explicit charset differ.
- Zero dates (`0000-00-00`) are permitted under 5.7's laxer default `sql_mode`
  and rejected by TiDB's default.
- `ZEROFILL` and integer display widths are deprecated in 8.0 and ignored by TiDB.
- 5.7's `utf8mb4_general_ci` default differs from 8.0's `utf8mb4_0900_ai_ci`,
  so identical DDL sorts differently on the two versions.
