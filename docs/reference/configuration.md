# Configuration reference

Detailed YAML settings and path rules for [Tkn ManicTime Data Pipeline](../../README.md).
For report-specific settings and editable rules, see the [report guide](../reports.md).

## Versions and validation

Each YAML file declares `schema_version: "2.3.0"`. Versions 2.0.x through 2.3.x are accepted;
unsupported major/minor versions, duplicate/unknown keys and incorrect types are errors.
Each layer is validated before merging, so a higher layer cannot hide an invalid lower layer.
Published state and run records use schema 3.0.0 and the latest Raw layout.
Other stored-data formats are rejected; the CLI does not convert them automatically.

## Configuration layers and profile selection

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

## Settings

| Key | Meaning |
| --- | --- |
| `default_profile` | Profile used unless `--profile` is supplied |
| `raw_path` | Parent folder for DB copies; default `~/.tkn/manictime_data_pipeline/data/raw` |
| `processed_data_path` | Parent folder for extracted CSV data; default `~/.tkn/manictime_data_pipeline/data/csv` |
| `report_path` | Parent folder for HTML reports; default `~/.tkn/manictime_data_pipeline/reports` |
| `state_path` | Durable checkpoint, provenance, run records and recovery data; default `~/.tkn/manictime_data_pipeline/state` |
| `backup_timeout_seconds` | Per-DB backup timeout; default 300, integer 1–86400 |
| `max_activity_drop_percent` | Maximum allowed activity row reduction; default 10, number 0–100; see the [safeguard](ingest.md#activity-reduction-safeguard) |
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

## Paths and historical profiles

For example, a historical profile can use separate parents while other profiles use the defaults:

```yaml
schema_version: "2.3.0"
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
