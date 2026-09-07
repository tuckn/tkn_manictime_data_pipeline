# ManicTime Data Pipeline

[日本語](README_ja.md)

Save the latest copies of ManicTime's SQLite databases as Raw and export reusable CSV.
The first run exports all available activity history and related tables; later runs
replace only changed month/table CSVs at fixed paths. Raw keeps one current generation;
CSV folders contain the latest data, and state holds provenance and checkpoints.
Build local HTML reports from ingested CSV, with per-PC year/month/week views,
application and website time, time-of-day patterns and dictionary lookups.
AI advice and cross-source integration remain future work.

## Use it — from installation to the first result

The normal command writes its outputs. Add `--dry-run` to compare the source and previous
output without writing pipeline data, configuration, state, cache or temporary files.

| Command    | Result                                                                               |
| ---------- | ------------------------------------------------------------------------------------ |
| `ingest` | Capture both DBs, compare partitions, export changes and publish the latest manifest |
| `verify` | Check CSV, Raw checksums and the correspondence between the two                      |
| `build-report` | Create HTML reports and supporting CSV for all ingested PCs |

### Requirements and installation

Use Python 3.11+ with its standard `sqlite3` module and uv. Windows is the primary
tested environment. There is no dependency on a system `sqlite3.exe` or a running
ManicTime UI. PyYAML is installed with the package. The CLI operates locally without
network access or AI calls.

**Tested with ManicTime 2023.1.1.0 (64-bit), Standard (free edition).**

Replace the example repository path with your checkout:

```console
cd "C:\path\to\tkn_manictime_data_pipeline"
uv tool install .
tkn-manictime-pipeline --help
tkn-manictime-pipeline config init
```

Edit the displayed `~/.tkn/manictime_data_pipeline/config.yaml`:

- `profiles.current-pc.source_path`: the ManicTime application folder containing `Data`, or the DB folder itself.
- `profiles.current-pc.device_id`: a name used for the data subfolder; it must be unique across profiles.
- `raw_path`: parent folder for saved copies of the ManicTime databases. Defaults to `~/.tkn/manictime_data_pipeline/data/raw`.
- `processed_data_path`: parent folder for extracted CSV data. Defaults to `~/.tkn/manictime_data_pipeline/data/csv`.

Only `source_path` and `device_id` need to be customized to start with the default output folders.
Both output paths are common settings, with optional per-profile overrides using the same rules.
The CLI appends `device_id` once to either parent path; do not include it yourself.

Both `ManicTimeCore.db` and `ManicTimeReports.db` must be present.
Keep Raw, CSV and execution-record folders outside the folder specified by `source_path`.
That is the ManicTime application folder when you specify the application root, or the DB
folder when you specify it directly. Input and output folders must not contain one another.
The packaged [configuration example](src/manictime_pipeline/resources/config.example.yaml)
contains the complete initial configuration.

```console
tkn-manictime-pipeline config show
tkn-manictime-pipeline ingest --dry-run
tkn-manictime-pipeline ingest
tkn-manictime-pipeline verify
```

Repeat `ingest` daily or weekly. It includes history on the first run and compares against
the previous successful publication thereafter. It also captures today's committed data;
an activity that is still being extended is updated on the next run.

### Find and use the results

The command returns absolute output paths as JSON on stdout and progress on stderr.

| Location | Purpose |
| --- | --- |
| `<raw_path>/<device_id>/ManicTimeCore.db` | Latest successful Core snapshot |
| `<raw_path>/<device_id>/ManicTimeReports.db` | Latest successful Reports snapshot |
| `<processed_data_path>/<device_id>/Ar_Activity/YYYY/MM.csv` | Current activity data for each month |
| `<processed_data_path>/<device_id>/<table>/all.csv` | Current related/metadata table |
| `<state_path>/<profile>/current.json` | Checkpoint pointing to the latest successful run record |
| `<state_path>/<profile>/runs/<run-id>.json` | Capture metadata, schema, CSV hashes/counts, effective settings and results |
| `<state_path>/<profile>/transactions/<run-id>/` | Temporary staging, rollback copies and recovery journal |

`state_path` defaults to `~/.tkn/manictime_data_pipeline/state`. It also holds the process
locks. **State is durable application data, not disposable cache. Back it up together
with Raw and CSV.** Removing it loses the checkpoint; ingest refuses to adopt existing CSVs.
The current Raw folder contains both DBs. During updates only, a temporary `.partial` folder
holds the candidate/previous DBs. Capture times, source paths, sizes and hashes are in the state run record.
The format version is recorded in JSON metadata; no version folder is added to the CSV path.

After a successful ingest, read activity CSVs directly in Power BI, Python or another tool:

```python
from pathlib import Path

root = Path("C:/path/to/csv/Example Current PC")
activity_csvs = sorted((root / "Ar_Activity").glob("*/*.csv"))
groups_csv = root / "Ar_Group" / "all.csv"
```

No manifest lookup is needed for ordinary data use. For auditing, read the state
`current.json` and the run record it names. Artifact paths are relative to the device's CSV folder.
Historical run records describe hashes at that time; the fixed Raw and CSV paths contain
current data. Old run records alone cannot recreate a past DB or CSV version. Re-extract the
current data from latest Raw; retain separate backups if historical restoration is required.

`all.csv` contains all rows of one related/metadata table at the last successful update,
for example `Ar_Group/all.csv` or `Ar_Timeline/all.csv`. Only `Ar_Activity` is split by month.

Read Raw and CSV **after ingest succeeds**, not while it is running. Replacement is per file;
an arbitrary reader can see different run versions across multiple files during an update.

## HTML activity reports

Use `tkn-manictime-pipeline build-report` to generate reports for all configured PCs and open
`<report_path>/index.html`. For weekly scheduled runs, use `build-report --no-open` after ingest.
The default output is `~/.tkn/manictime_data_pipeline/reports`; set the top-level `report_path`
in YAML to change it. `--dry-run` validates and aggregates without writes or browser launch.
See [report setup, metrics, mappings and outputs](docs/reports.md).

## Command reference

| Purpose                                                 | Command                     |
| ------------------------------------------------------- | --------------------------- |
| Create the user configuration without overwriting edits | `config init [--dry-run]` |
| Inspect merged values and their sources                 | `config show`             |
| Inspect source schema, row counts and activity range    | `inspect`                 |
| Capture and incrementally publish CSV                   | `ingest [--dry-run]`      |
| Verify the current dataset and its Raw capture          | `verify`                  |
| Build year/month/week HTML for all PCs | `build-report [--dry-run] [--no-open]` |
| Recover an interrupted CSV update | `recover [--dry-run]` |

Common options: `--config PATH`, `--profile NAME`, `-q/--quiet` and `-v/--verbose`.
They work before or after the command. Quiet and verbose are mutually exclusive.
`config init` always targets the user configuration and rejects `--config`/`--profile`.
It returns unchanged when the template already exists; an edited file is preserved with an error.

Exit codes are 0 for success, 1 for expected processing/configuration failures,
2 for command-line usage errors and 130 for interruption.
`--quiet` suppresses non-error stderr messages, while result JSON still goes to stdout.
`--verbose` includes error tracebacks. ANSI color is limited to supported interactive
terminals and respects `NO_COLOR`.

`ingest` returns `created`, `updated` or `unchanged` for the CSV dataset.
Even an unchanged CSV run captures both DBs and records a new execution. It updates the
latest Raw if its bytes differ; it does not accumulate an archive for every invocation.
`removed` counts partitions removed from both the current index and the fixed CSV paths.
Only tracked derived data is replaced/removed. Source DBs are preserved; superseded current
Raw is deleted after a successful commit.

## Configuration details

Each YAML file declares `schema_version: "2.2.0"`. Versions 2.0.x through 2.2.x are accepted;
unsupported major/minor versions, duplicate/unknown keys and incorrect types are errors.
Each layer is validated before merging, so a higher layer cannot hide an invalid lower layer.
Published state and run records use schema 3.0.0 and the latest Raw layout.
Other stored-data formats are rejected; the CLI does not convert them automatically.

Precedence is built-in defaults → user config → current directory's `.tkn/config.yaml`
→ `--config` → CLI profile selection. Profiles merge by name and then by property.
`config show` includes source versions, the effective schema version, resolved paths and
the source that supplied each value. No configuration file is written while reading it.

Without `--config`, both the user config at `~/.tkn/manictime_data_pipeline/config.yaml`
and the current directory's `.tkn/config.yaml` are discovered automatically; missing optional
files are skipped. This merges settings, rather than selecting only the first existing file.
An explicitly supplied `--config` path must exist; a missing file is an error.
`--profile` selects a profile name within the merged settings, not a config file or folder.
When omitted, `default_profile` must match a key in `profiles`. For example, after renaming
`profiles.current-pc` to `profiles.desktop`, also set `default_profile: desktop`.
The CLI does not guess another profile when the selected name is missing. The error lists
the selected name, its source, available profiles and loaded config paths. Different profile
names remain separate after merging, even if their `device_id` values match; the duplicate
device error identifies the conflicting profiles and their config paths.


| Key | Meaning |
| --- | --- |
| `default_profile` | Profile used unless `--profile` is supplied |
| `raw_path` | Parent folder for DB copies; default `~/.tkn/manictime_data_pipeline/data/raw` |
| `processed_data_path` | Parent folder for extracted CSV data; default `~/.tkn/manictime_data_pipeline/data/csv` |
| `state_path` | Durable checkpoint, provenance, run records and recovery data; default `~/.tkn/manictime_data_pipeline/state` |
| `backup_timeout_seconds` | Per-DB backup timeout; default 300, integer 1–86400 |
| `max_activity_drop_percent` | Maximum allowed activity row reduction; default 10, number 0–100; see below |
| `profiles.<name>.device_id` | Required name used for the data subfolder; unique across profiles and valid on Windows |
| `profiles.<name>.source_path` | Required ManicTime application folder or DB folder |
| `profiles.<name>.raw_path` | Optional override of the common DB-copy parent folder |
| `profiles.<name>.processed_data_path` | Optional override of the common CSV parent folder |

For each output path, a profile override takes precedence over the common setting; otherwise
the common value is used. This also applies when a lower-priority file sets a profile override
and a higher-priority file changes the common value. Set that profile's value in the higher
file to override it. Omitted common values use the built-in defaults.
`config show` includes `effective_profiles`, showing the inherited/overridden parent folders,
actual per-device output folders and the source that supplied each parent value.

For example, a historical profile can use separate parents while other profiles use the defaults:

```yaml
schema_version: "2.0.0"
default_profile: historical-pc
profiles:
  historical-pc:
    device_id: Example Historical PC
    source_path: C:/path/to/historical/ManicTime
    raw_path: C:/path/to/archive
    processed_data_path: C:/path/to/csv
```

The paths above produce `C:/path/to/archive/Example Historical PC/*.db` and
`C:/path/to/csv/Example Historical PC/`. There is no additional `Raw` folder.
Changing `device_id` changes the output folder; do not rename it to relabel existing data.

`~` expands to the current user's home. Relative paths in every layer resolve from
the execution working directory, not the YAML file's directory. An installed CLI does not
implicitly read the checkout's configuration unless it runs from that checkout or
`--config` names it. Real settings, DBs and runtime results must stay outside Git.

Add a separate named profile with a distinct `device_id` for a historical DB.
Choose a Raw destination separate from the archived input directory.
Only one selected profile runs per invocation; other sources are not automatically ingested.
A published dataset is bound to its resolved source path, Raw root and device ID.
The CSV root and profile name are also bound to state. Changing these bindings is rejected.
Moving existing data or renaming a profile requires a separate, verified procedure.

### Task Scheduler

Configure a daily or weekly task to run the installed executable. Use its absolute path
from `Get-Command tkn-manictime-pipeline` and these arguments:

```console
--config "C:\path\to\config.yaml" --profile current-pc ingest
```

Use absolute data paths in scheduled settings. The task runs as the account whose home
contains the selected configuration/state. Set Task Scheduler to avoid overlapping runs;
the CLI also holds OS locks in state, keyed by the resolved Raw and CSV roots. All writers
sharing data roots must use the same `state_path` and Windows account. No browser opens.
This repository does not automatically register, replace or disable scheduled tasks.

## Storage, extraction and recovery contract

### Raw capture

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

### Activity reduction safeguard

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
schema_version: "2.1.0"
max_activity_drop_percent: 100
```

```console
tkn-manictime-pipeline --config "C:\path\to\approved-reduction.yaml" ingest --dry-run
tkn-manictime-pipeline --config "C:\path\to\approved-reduction.yaml" ingest
```

Keep your existing profile/output settings loaded from the normal configuration. The next
invocation without that override uses the normal limit again. There is no implicit retry
with a relaxed threshold and no force-overwrite switch for edited/unmanaged files.

### What is extracted

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

### Incremental behavior and identity

A run scans every exported source row in primary-key order, computing a SHA-256 fingerprint
for each month/table partition. It also checks prior published CSV hashes before updating.
This deliberately avoids an ID/sequence-only watermark: a past edit, deleted row, late
arrival, modified icon or activity moved to another month must be detected.

Only changed partitions are written. An updated month replaces the same CSV path with
all rows for that month. Unchanged CSVs retain their paths and modification times. A second streaming pass reads changed tables
to write selected partitions; memory does not grow with the number of activity rows.
This is incremental **output**, with full-source comparison cost on each run.

The complete run record referenced by state/current.json is the checkpoint.
A generated dataset UUID is preserved across
runs; record identity is dataset UUID + table + source primary key.
Source schema changes trigger new versions of affected partitions. Missing required
tables/keys, unkeyed export tables and invalid activity dates stop publication.

`verify` checks pointer/manifest hashes, all currently referenced CSV hashes, headers,
column/row counts, both current Raw DB hashes and integrity, and freshly recomputed
partition fingerprints against that Raw Reports snapshot. It does not compare against
the continually changing live DB or audit every unreferenced historical run.

### Failure and retry

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

```console
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

## Maintenance and development

After changing source, packaged resources or dependencies:

```console
cd "C:\path\to\tkn_manictime_data_pipeline"
uv tool install . --reinstall
tkn-manictime-pipeline --version
```

For development:

```console
cd "C:\path\to\tkn_manictime_data_pipeline"
uv sync --group dev
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv build
```

Tests use synthetic SQLite files and include WAL capture, source preservation, historical
edits/deletions, idempotency, schema changes, output corruption, process death between Raw moves, during CSV replacement and after commit, rollback,
Raw retention bounds, activity reduction safeguards,
unmanaged-file collisions, state failures, locks, configuration layering, Unicode/BLOB/NULL
CSV and stderr/JSON behavior.
Runtime modules are split into CLI/configuration, SQLite access, streaming CSV, pipeline
publication and file/logging helpers. No live/private datasets are included in tests.
See [the output format example](docs/manifest.example.json) for a small synthetic index.

The design follows the existing Itadaki pipeline's src layout, uv packaging, YAML profiles,
application-owned state and default-write/dry-run command contract. ManicTime's continuously
updated DB requires snapshot acquisition and revision-aware partitions, so Itadaki's
processed-source deletion behavior is intentionally not carried over.
