# ManicTime Data Pipeline

[日本語](README_ja.md)

Save ManicTime's SQLite databases as immutable Raw captures and export reusable CSV.
The first run exports all available activity history and related tables; later runs
publish only changed month/table partitions. Old captures and CSV versions remain
available. Version 0.2 covers acquisition, extraction, execution records and verification;
HTML reports, classification rules, AI advice and cross-source integration are future work.

## Use it — from installation to the first result

The normal command writes its outputs. Add `--dry-run` to compare the source and previous
output without writing pipeline data, configuration, state, cache or temporary files.

| Command    | Result                                                                               |
| ---------- | ------------------------------------------------------------------------------------ |
| `ingest` | Capture both DBs, compare partitions, export changes and publish the latest manifest |
| `verify` | Check CSV, Raw checksums and the correspondence between the two                      |

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

| Location                                                              | Purpose                                           |
| --------------------------------------------------------------------- | ------------------------------------------------- |
| `<raw_path>/<device_id>/<run-id>/`                                          | Both DB snapshots plus`capture.json`            |
| `<processed_data_path>/<device_id>/pipeline-v1/current.json`        | Entry point to the current complete dataset       |
| `.../pipeline-v1/runs/<run-id>/manifest.json`                       | Complete partition index, schema and provenance   |
| `.../pipeline-v1/runs/<run-id>/Ar_Activity/YYYY/MM.csv`             | New versions of changed activity months           |
| `.../pipeline-v1/runs/<run-id>/<table>/all.csv`                     | New versions of changed dimension/metadata tables |
| `~/.tkn/manictime_data_pipeline/state/<profile>/runs/<run-id>.json` | Execution status, counts and failures             |

Read `current.json`, then the manifest it names, and load only the paths in
`manifest.artifacts`. Paths are relative to `pipeline-v1`. An unchanged partition can
point to an earlier run. **Do not recursively combine every CSV under `runs`**:
that would include historical versions more than once.

For example, this standard-library code lists the current activity CSVs:

```python
import json
from pathlib import Path

root = Path("C:/path/to/ManicTime/Example Current PC/pipeline-v1")
pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
manifest = json.loads((root / pointer["manifest"]).read_text(encoding="utf-8"))
activity_csvs = [
    root / item["path"] for item in manifest["artifacts"].values() if item["table"] == "Ar_Activity"
]
```

Existing PowerShell-exported CSVs outside `pipeline-v1` are left in place.
There is no automatic migration or removal of those files.

## Command reference

| Purpose                                                 | Command                     |
| ------------------------------------------------------- | --------------------------- |
| Create the user configuration without overwriting edits | `config init [--dry-run]` |
| Inspect merged values and their sources                 | `config show`             |
| Inspect source schema, row counts and activity range    | `inspect`                 |
| Capture and incrementally publish CSV                   | `ingest [--dry-run]`      |
| Verify the current dataset and its Raw capture          | `verify`                  |

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
Even an unchanged run saves a new Raw capture and execution manifest.
`removed` partition counts mean removal from the current manifest, not deletion from disk.

## Configuration details

Each YAML file must declare `schema_version: "2.0.0"`. Version 2.0.x is accepted;
unsupported major/minor versions, duplicate/unknown keys and incorrect types are errors.
Each layer is validated before merging, so a higher layer cannot hide an invalid lower layer.

Precedence is built-in defaults → user config → current directory's `.tkn/config.yaml`
→ `--config` → CLI profile selection. Profiles merge by name and then by property.
`config show` includes source versions, the effective schema version, resolved paths and
the source that supplied each value. No configuration file is written while reading it.

| Key | Meaning |
| --- | --- |
| `default_profile` | Profile used unless `--profile` is supplied |
| `raw_path` | Parent folder for DB copies; default `~/.tkn/manictime_data_pipeline/data/raw` |
| `processed_data_path` | Parent folder for extracted CSV data; default `~/.tkn/manictime_data_pipeline/data/csv` |
| `state_path` | Execution records; default `~/.tkn/manictime_data_pipeline/state` |
| `backup_timeout_seconds` | Per-DB backup timeout; default 300, integer 1–86400 |
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

The paths above produce `C:/path/to/archive/Example Historical PC/<run-id>/` and
`C:/path/to/csv/Example Historical PC/pipeline-v1/`. There is no additional `Raw` folder.
Changing `device_id` changes the output folder; do not rename it to relabel existing data.

`~` expands to the current user's home. Relative paths in every layer resolve from
the execution working directory, not the YAML file's directory. An installed CLI does not
implicitly read the checkout's configuration unless it runs from that checkout or
`--config` names it. Real settings, DBs and runtime results must stay outside Git.

Add a separate named profile with a distinct `device_id` for a historical DB.
Choose a Raw destination separate from the archived input directory.
Only one selected profile runs per invocation; other sources are not automatically ingested.
A published dataset is bound to its resolved source path, Raw root and device ID.
Changing those bindings is rejected, except for the documented v0.1-to-v0.2 Raw layout
transition below. Other storage migrations require a separate deliberate procedure.

### Task Scheduler

Configure a daily or weekly task to run the installed executable. Use its absolute path
from `Get-Command tkn-manictime-pipeline` and these arguments:

```console
--config "C:\path\to\config.yaml" --profile current-pc ingest
```

Use absolute data paths in scheduled settings. The task runs as the account whose home
contains the selected configuration/state. Set Task Scheduler to avoid overlapping runs;
the CLI also holds OS file locks on the Raw and processed roots. No browser opens.
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

Every capture includes start/end UTC times, source paths, file sizes, SHA-256, tool
version, SQLite journal mode and capture method. DB integrity is checked with
`PRAGMA quick_check`. Source databases are opened read-only and are never pruned,
vacuumed, overwritten or deleted. Snapshot files are never overwritten.
All tables, including internal and aggregate tables, remain in Raw.

Storage grows by approximately the combined DB sizes per run, plus changed CSV partitions.
For example, two DBs totalling 754 MB add about 23 GB of Raw over 30 daily runs.
No retention/cleanup command is included in v0.2. Historical CSV files are also retained;
older runs may still contain partitions referenced by the current manifest.

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

Only changed partitions are written. An updated month is a complete replacement version
of that month, not an append-only batch. A second streaming pass reads changed tables
to write selected partitions; memory does not grow with the number of activity rows.
This is incremental **output**, with full-source comparison cost on each run.

The complete manifest is the checkpoint. A generated dataset UUID is preserved across
runs; record identity is dataset UUID + table + source primary key.
Source schema changes trigger new versions of affected partitions. Missing required
tables/keys, unkeyed export tables and invalid activity dates stop publication.

`verify` checks pointer/manifest hashes, all currently referenced CSV hashes, headers,
column/row counts, both current Raw DB hashes and integrity, and freshly recomputed
partition fingerprints against that Raw Reports snapshot. It does not compare against
the continually changing live DB or audit every unreferenced historical run.

### Failure and retry

Raw is first written to a new `.partial` directory, verified and renamed. CSVs and their
manifest are then staged and verified. Only after all outputs are complete does atomic
replacement publish `current.json`. Prior captures/CSVs remain available.
This commit boundary prevents readers using the manifest from seeing a half-published run.

Failures after run creation are recorded under `state/<profile>/runs`.
A failed/incomplete Raw or export directory is retained for diagnosis. Completed Raw remains
available even if extraction fails. A retry creates a new run and compares against the last
success; v0.2 does not resume partway through a failed capture or delete leftovers.
Configuration/preflight failures are reported on stderr before a run is created.
Operational state is supplementary: if its final write fails, the published manifest
remains authoritative and the CLI warns.

OS locks are released on normal exit or process termination; the small lock files remain.
A forced termination can leave a status of running in its operational record.
The authoritative success indicator is the manifest referenced by `current.json`.
Manually edited/missing published CSVs stop ingest and verify; restore them from your
backup or re-extract to a separate processed root. There is no force-overwrite switch.
Concurrent manual edits/storage synchronization are outside the process-lock boundary.

Dry-run reads the live Reports DB in one transaction, which can briefly delay writers
on rollback-journal databases. It skips backup and integrity checking, so it cannot prove
destination permissions, free space or successful backup. SQLite itself manages its
normal locking/shared-memory facilities; the pipeline issues no source write statements.

## Maintenance and development

### Upgrade from v0.1

The application is v0.2.0 and the configuration schema is 2.0.0. The output manifest
schema remains 1.0.0: its existing `csv_contract.encoding` field identifies the CSV format.
Version 1.0.x configurations are rejected with upgrade instructions rather than reinterpreted.

1. Back up every loaded config file shown by the old `config show`. An unchanged old sample
   can be replaced with the new packaged template after backing it up; preserve your edits.
2. Set each config's `schema_version` to `"2.0.0"`. Keep `source_path`, `device_id`,
   `processed_data_path` and `state_path` as intended. The old common CSV parent has the same meaning.
3. Old `profiles.<name>.raw_path` named a per-device archive directory. Set the new
   `raw_path` to its parent if its last folder is `device_id`. It may be common or per-profile.
   Both Raw and CSV paths now name parents, and the CLI appends `device_id` once.
4. Reinstall, then run `config show`, `ingest --dry-run`, `ingest` and `verify`.

For the standard v0.1 layout, old captures remain at `<raw_path>/<device_id>/Raw/<run-id>/`;
new captures go to `<raw_path>/<device_id>/<run-id>/`. No Raw files are moved or deleted.
The old current dataset remains verifiable before the first new ingest. The first update
writes BOM-free versions of all present CSV partitions, because removing BOM changes every
file hash. Old BOM CSVs and manifests remain untouched, and dataset identity is preserved.
Later runs reuse unchanged BOM-free CSVs. A failed update retains the previous current dataset.
If the old Raw archive folder does not end in `device_id`, this automatic layout transition
does not apply; do not merely change the path and expect existing data to be adopted.

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
edits/deletions, idempotency, schema changes, output corruption, failures before publication,
locks, strict configuration layering, Unicode/BLOB/NULL CSV and stderr/JSON behavior.
Runtime modules are split into CLI/configuration, SQLite access, streaming CSV, pipeline
publication and file/logging helpers. No live/private datasets are included in tests.
See [the output format example](docs/manifest.example.json) for a small synthetic index.

The design follows the existing Itadaki pipeline's src layout, uv packaging, YAML profiles,
application-owned state and default-write/dry-run command contract. ManicTime's continuously
updated DB requires snapshot acquisition and revision-aware partitions, so Itadaki's
processed-source deletion behavior is intentionally not carried over.
