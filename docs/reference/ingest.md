# Ingest, CSV and recovery reference

This reference describes how the CLI captures ManicTime databases, publishes CSV and
recovers interrupted updates. Start with the
[README's first-run steps](../../README.md#create-the-first-report) for normal use.

## Raw capture

Both databases are saved with Python's [SQLite backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).
This uses SQLite's [online backup mechanism](https://www.sqlite.org/backup.html), rather
than a filesystem copy of a file being changed. Committed WAL contents are included;
uncommitted work and unsaved application buffers are not.

Each DB is independently consistent. The Core and Reports backups are sequential,
**not one application-wide transaction**. Reports may lag Core. The capture is data
evidence, not a complete backup of ManicTime executables, settings, plugins or screenshots.

Each state run record includes capture start/end UTC times, source paths, file sizes, SHA-256, tool
version, SQLite journal mode and capture method. DB integrity is checked with
`PRAGMA quick_check`. Source databases are opened read-only and are never pruned,
vacuumed, overwritten or deleted. Current Raw copies are replaced only after validation.
All tables, including internal and aggregate tables, remain in Raw.

Raw keeps **one generation normally and old + new during an ordinary update**.
For example, two databases totalling 754 MB need about 754 MB in steady state, and roughly
1.5 GB during replacement when their sizes are similar, plus temporary CSV space.
Storage follows DB growth rather than the number of runs. The previous DBs are moved
aside on the same filesystem, not copied into a third generation in state.

Candidates from failed or rejected updates are discarded after successful rollback;
the failure details and capture metadata remain in state. If recovery/cleanup cannot
finish, staging is retained and the next update must recover before capturing again.

## Activity reduction safeguard

Before replacing Raw or CSV, ingest compares `Ar_Activity` row counts with the previous
successful snapshot, both overall and for each existing `ReportId` timeline. By default,
a reduction **greater than 10%** in any comparison stops publication. A disappeared
nonempty timeline counts as a 100% reduction, even if other timelines have grown.
Normal updates and `ingest --dry-run` use the same check. A rejected dry-run returns
an error without writing; a rejected normal run preserves previous data, discards the
candidate after recovery and records the counts/reason in state.

`max_activity_drop_percent` is a common setting (not a per-profile setting). `0` disallows
any decrease; `100` allows any decrease, including an empty history. Exactly the configured
limit is allowed. It is a row-count safeguard, not proof that the new DB contains every
previous record: small losses, same-count substitutions or gradual losses can pass.
It does not classify edits/deletions as errors by themselves.

If a reduction is intentional, inspect the source and retain an independent backup as
needed. Use a temporary explicit configuration override for the selected invocation:

```yaml
schema_version: "2.3.0"
max_activity_drop_percent: 100
```

```shell
tkn-manictime-pipeline --config "C:\path\to\approved-reduction.yaml" ingest --dry-run
tkn-manictime-pipeline --config "C:\path\to\approved-reduction.yaml" ingest
```

Keep your existing profile/output settings loaded from the normal configuration. The next
invocation without that override uses the normal limit again. There is no implicit retry
with a relaxed threshold and no force-overwrite switch for edited/unmanaged files.

## What is extracted

The exporter preserves original table/column names and all columns, including icons,
source IDs, change sequences and opaque metadata. The manifest records SQL definitions,
column types and primary keys. Non-aggregate `Ar_*` tables from Reports are exported.
Tables ending in `ByHour`, `ByDay` or `ByYear`, and `Ar_TimelineSummary`,
are retained in Raw but excluded from CSV. Non-`Ar_*` internal tables are Raw-only.

Minimum supported tables/keys are:

| Table              | Primary key              | Relation/use                               |
| ------------------ | ------------------------ | ------------------------------------------ |
| `Ar_Activity`    | `ReportId, ActivityId` | Activity intervals and source text         |
| `Ar_Timeline`    | `ReportId`             | Interpret each timeline through its schema |
| `Ar_Group`       | `ReportId, GroupId`    | Join from activity using both columns      |
| `Ar_CommonGroup` | `CommonId`             | Common group definitions                   |

`GroupId` may be null for tag/group-list activities. Preserve `Ar_GroupList` and
`Ar_GroupListItem` for those relationships. Timeline report IDs must not be assumed
to be identical across installations. Time on different timelines can overlap; do not
sum all activity durations as total PC usage.

Activity partitions use the year/month of the source `StartLocalTime`.
An interval crossing midnight/month-end is not split. Months with no rows have no activity CSV;
empty metadata tables have a header-only CSV. Source `*UtcTime` columns represent UTC and
`*LocalTime` columns represent recorded local wall time. Their original strings are kept,
without inventing an IANA timezone or adding an inferred offset.
Manifest timestamps are offset-qualified ISO 8601 UTC.

CSV uses UTF-8 without BOM, comma separators, LF, headers and CSV quoting for commas/quotes/newlines.
Numbers use their source representation. SQL null is `\N`; BLOB is `\B` followed by
base64; a text value beginning with backslash receives one extra leading backslash.
LF terminates CSV records; CR, LF and CRLF inside source values are preserved and quoted.
Empty string, null, bytes and literal marker text therefore remain distinct.
`manictime_pipeline.export.decode_cell` decodes these markers.
Import CSV columns as data/text in spreadsheets: source titles are preserved literally,
including strings that a spreadsheet could otherwise interpret as formulas.

## Incremental behavior and identity

A run scans every exported source row in primary-key order, computing a SHA-256 fingerprint
for each month/table partition. It also checks prior published CSV hashes before updating.
This deliberately avoids an ID/sequence-only watermark: a past edit, deleted row, late
arrival, modified icon or activity moved to another month must be detected.

Only changed partitions are written. An updated month replaces the same CSV path with
all rows for that month. Unchanged CSVs retain their paths and modification times.
A second streaming pass reads changed tables to write selected partitions; memory
does not grow with the number of activity rows.
This is incremental **output**, with full-source comparison cost on each run.

The complete run record referenced by `<state_path>/<profile>/current.json` is the
checkpoint. A generated dataset UUID is preserved across runs; record identity is
dataset UUID + table + source primary key.
Source schema changes trigger new versions of affected partitions. Missing required
tables/keys, unkeyed export tables and invalid activity dates stop publication.

`verify` checks pointer/manifest hashes, all currently referenced CSV hashes, headers,
column/row counts, both current Raw DB hashes and integrity, and freshly recomputed
partition fingerprints against that Raw Reports snapshot. It does not compare against
the continually changing live DB or audit every unreferenced historical run.

## Failure and retry

Candidate DBs are captured and checked in `<raw_path>/<device_id>/.<run-id>.partial/new/`.
Completed snapshots are read without creating SQLite WAL/shared-memory files; live source
reads retain normal SQLite locking and include committed WAL data. Unexpected SQLite
sidecar files beside saved Raw stop validation instead of being ignored or deleted.
All changed CSVs are generated/checked in `state/<profile>/transactions/<run-id>/new/`.
CSV rollback copies go to the transaction's `old/` folder. All provenance and the recovery
journal stay in state; no JSON metadata is written under Raw or CSV.

After preparation, the old current DBs are moved to the Raw staging `old/` directory and
candidate DBs move into their fixed paths. CSV replacements use same-directory temporary
files, including when CSV and state reside on different drives. Both Raw and CSV are
verified before writing the run record and committing state `current.json`. Old DBs,
CSV rollback copies and staging are removed only after the commit.

An ordinary failure attempts rollback to the previous Raw and CSV contents. If rollback is blocked
(for example, a file remains open or has been manually edited), staging and the journal
remain in state. A terminated process also leaves its journal. Run:

```shell
tkn-manictime-pipeline recover --dry-run
tkn-manictime-pipeline recover
tkn-manictime-pipeline verify
```

`recover --dry-run` lists pending runs without changing files. `recover` rolls back
uncommitted updates; if the checkpoint was already committed, it only clears staging.
It checks hashes and refuses to overwrite unrelated manual edits. The next ordinary
`ingest` also recovers pending transactions before starting a new run. Read-only ingest
and verify stop if a transaction is pending. Automatic recovery does not resume extraction.

State writes are required for publication. A failure to create the initial run record stops
before Raw or CSV writes. Completed run records referenced by the checkpoint are immutable;
records from interrupted/uncommitted attempts can be marked failed during recovery.
The authoritative commit marker is `current.json`, not a run file's status alone.
Failed candidates are cleaned after rollback; the previous successful Raw stays available.
Historical run records remain in state. Configuration/preflight errors are reported on stderr.

OS locks are released on process exit; the small state lock files remain. Filesystem sync
software, manual edits and arbitrary CSV readers do not participate in those locks.
Fixed paths do not provide a transaction spanning multiple files. Do not use Raw or CSV
after a failed/interrupted update until recovery completes. Filesystem or storage failures
that prevent rollback require restoring the affected files/state from backups.

Manually edited/missing tracked CSVs and any untracked CSVs in the device CSV
folder stop ingest and verify. Preserve untracked data elsewhere or restore tracked data;
there is no force-overwrite option. Source DBs are always read-only.

Dry-run reads the live Reports DB in one transaction, which can briefly delay writers
on rollback-journal databases. It skips backup and integrity checking, so it cannot prove
destination permissions, free space or successful backup. SQLite itself manages its
normal locking/shared-memory facilities; the pipeline issues no source write statements.
