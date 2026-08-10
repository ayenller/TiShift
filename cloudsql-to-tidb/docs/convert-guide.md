# Convert guide

```bash
mysqldump --no-data --routines --triggers --events \
  -h 127.0.0.1 -u USER myapp > source-schema.sql

tishift-cloudsql convert --ddl-file source-schema.sql --dry-run
tishift-cloudsql convert --ddl-file source-schema.sql
```

Outputs `converted-schema.sql`, `ddl-cleanup-report.json`, and
`ddl-cleanup-report.md` into `--output-dir` (default `./tishift-reports`).

Exits 1 if any rewrite left syntax that no longer parses.

## Rules

| Rule | Trigger | Action | Auto |
|---|---|---|---|
| CSQL-DDL-1 | `DEFINER=user@host` | commented out | yes |
| CSQL-DDL-2 | `ENGINE=MyISAM\|MEMORY\|ARCHIVE\|CSV\|BLACKHOLE\|FEDERATED` | rewritten to `InnoDB` | yes |
| CSQL-DDL-3 | `ROW_FORMAT`, `KEY_BLOCK_SIZE`, `ENCRYPTION`, `COMPRESSION`, `TABLESPACE`, `STATS_*` | commented out | yes |
| CSQL-DDL-4 | `utf8`/`utf8mb3` charset or collation | widened to `utf8mb4` | yes |
| CSQL-DDL-5 | `FULLTEXT KEY` | kept + TiFlash replica emitted | partial |
| CSQL-DDL-6 | `SPATIAL KEY` | commented out + review note | partial |
| CSQL-DDL-7 | `AUTO_INCREMENT` single-column PK | suggestion only | no |

`CSQL-DDL-1` runs on every statement type; the rest only inside
`CREATE TABLE` / `ALTER TABLE`.

## What the rules will not do for you

**CSQL-DDL-2** rewrites the clause, not the semantics. MyISAM is
non-transactional and its `FULLTEXT` behaviour differs; if the application
relied on either, the rewrite is the start of the work, not the end.

**CSQL-DDL-4** widens the column. Sort order changes, and an index key grows
from 3 to 4 bytes per character — a `VARCHAR(255)` index goes from 765 to 1020
bytes. Re-verify anything relying on collation-dependent uniqueness or ordering.

**CSQL-DDL-6** comments out the index. It does **not** retype the spatial
columns — that is BLOCKER-4, an application rewrite (convert to `JSON`, move
geometry logic into the application), and no tool should guess at it.

**CSQL-DDL-7** never fires on a composite primary key, because `AUTO_RANDOM`
cannot replace the leading column of one. Even on a single-column key it only
suggests: `AUTO_RANDOM` changes the ID values the application observes.

## Nothing is deleted, and re-running is safe

Every removed clause survives as `/* TISHIFT-REMOVED [rule]: … */`; every
rewritten clause keeps its original in the same comment. Review notes are
`TISHIFT-REVIEW`, deferred actions are `TISHIFT-INFO`. Only plain `/* */` and
`--` comments are emitted — never `/*! */` executable comments.

Rules match against a length-preserving *mask* of the SQL in which string
literals and comments are blanked out. Text already inside a `TISHIFT-*` comment
therefore cannot re-match, which makes `convert` idempotent: run it over its own
output and you get the same file back.

## TiFlash replicas

Outside Starter, TiDB parses `FULLTEXT KEY` but builds no index. The clause is
harmless so it is kept, and a TiFlash replica is emitted after the table:

```sql
ALTER TABLE articles SET TIFLASH REPLICA 2;
```

Columnar scans then accelerate `LIKE`/`REGEXP` filtering in place of the index.
`MATCH … AGAINST` queries still need rewriting — they will not use an index.

`--tiflash-replicas 0` downgrades the `ALTER` to a `TISHIFT-INFO` comment
carrying the command to run later.

**Timing trade-off:** replicas created before the load mean TiFlash replicates
during the import, which slows large loads. Move the `ALTER`s to the end of the
script if import speed matters.

## Applying the output

```sql
SET FOREIGN_KEY_CHECKS=0;
SOURCE converted-schema.sql;
SET FOREIGN_KEY_CHECKS=1;
```

Per-table DDL is not FK-topologically ordered, so an unwrapped apply dies with
`ERROR 1824` on the first forward reference.

## Parse validation

Rewritten statements are re-parsed with sqlglot. A failure is only reported when
the **original parsed and the rewritten form did not** — sqlglot's MySQL dialect
has gaps of its own (`SRID` on a spatial column is one), and a statement it could
never parse is not evidence that the rewrite broke anything.
