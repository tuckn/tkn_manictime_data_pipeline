# CLI result output contract

This contract applies to every command, including configuration operations, ingest,
inspection, validation, recovery, reports and the review server's final result.
`--help` and `--version` retain their normal informational output and exit with 0.

## Formats and streams

The default stdout is one `key=value` entry per line. Nested mappings use dots; arrays
and SQLite tuples use indexes such as `[0]`. Empty arrays/maps appear as `[]`/`{}`,
booleans as `true`/`false`, and unset values as `null`. Strings have no JSON quotes.
Paths retain their complete spelling and single Windows backslashes, without truncation.
CR/LF/tab are escaped as `\r`/`\n`/`\t`; other control characters and Unicode line
separators use `\uXXXX`. This escaping also applies to keys. Text output is for people;
its strings are not a reversible machine serialization.

`--json` selects one valid JSON document on stdout, with no headings, logs, progress
or ANSI color. UTF-8 is used in both formats. TTY, pipes and redirection do not select
a format. Progress, warnings and diagnostics go to stderr. `--quiet` suppresses logs
below ERROR; `--verbose` adds diagnostics and error tracebacks. Neither flag suppresses
results or changes their format. They cannot be combined.

`--json` and `--full-output` are common options, accepted before or after commands and
at each group level (`--json config list`, `config --json list`, `config list --json`).
They only affect presentation, never artifact selection, writes, browser launches or
dry-run behavior.

## Result fields

Success is a JSON object with `status: "success"`, `command` (a string such as
`"config init"`) and integer `exit_code: 0`, plus these command-specific fields.
Optional `counts` entries are nonnegative integers; action counters are present only
when that action occurs.

| Command | Fields and counts |
| --- | --- |
| `config list` | Existing `schema_version`, `values`, `sources`, `winning_sources`, `effective_profiles`, `user_config_path`, `selected_profile`, `migration_applied`; `counts.config_files` and `counts.profiles` |
| `config init` | `action` (`created`, `unchanged`, `would_create`), absolute `path`, `counts.<action>` |
| `rules init` | `items`: array of `{path: string, action: string}`; `counts.items` and per-action counters (`created`, `preserved`, `would_create`). Preserved files stay visible. |
| `inspect` | Existing database-name keys (`ManicTimeCore.db`, `ManicTimeReports.db`) with sizes, schemas, row counts and activity range; `counts.databases`, `counts.tables` |
| `ingest` | Existing `action`, `profile`, output paths and, on writes, `run_id`, `current`, `manifest`; `partitions` action counts, integer `rows`, `activity_rows`, `export_rows`, `changes` partition-key arrays. Dry-run also includes source/target paths, planned capture counts and activity-drop checks. |
| `verify` | `action: "verified"`, `run_id`, integer `partitions`, `rows`, `activity_rows`, absolute `current` |
| `recover` | `action`, `runs` with run IDs and recovery actions, `counts.runs` and per-action counts; dry-run instead has `pending_runs` and `counts.pending_runs` |
| `build-report` | Absolute `report_path` and `index`, boolean `dry_run`, `devices` array (device ID, action, reuse flag, days, periods, dictionary records); `counts.devices` and per-action counts |
| `review` | On normal server stop: `action: "stopped"`, absolute `report_path`, `index`. The server URL remains a stderr progress message. The final result arrives after the server stops. |

For ingest, `changes` contains partition keys relative to `processed_path`, without
the `.csv` suffix (for example `Ar_Activity/2026/01`). `partitions.unchanged` always gives
the count. By default, a nonempty `changes.unchanged` array is omitted and replaced by
`details_omitted: {"changes.unchanged": N}` and `details_hint` explaining `--full-output`.
Empty unchanged arrays and all created/updated/removed arrays remain visible.
`--full-output` includes the complete unchanged array in either format. No failure
or attention target is truncated. Other command results are shown in full.

## Failures and interruption

Failures have `status: "failed"` (or `"interrupted"` for exit 130), nullable `command`,
integer `exit_code`, `counts.errors`, and an `errors` array. Each error contains:

- `type`: exception class name, e.g. `ArgumentError`, `ValueError`, `PermissionError`.
- `reason`: human-readable reason. Do not extract paths from it.
- `targets`: array of `{role: string, path: string}`. Exact file context is used for
  configuration, source DB, CSV, Raw and JSON validation where available; otherwise
  these are the known input/output scope paths. Argument errors may have no targets.
- `activity_drop_check`, when relevant: the existing reduction limits and violations.

When a multi-file rules operation or report build has recorded items before raising,
the failure also includes `processed_items`, `counts.processed_items`,
`counts.actions` and `progress_note`. These describe known progress, not successful
publication or a rollback guarantee. Operations remain fail-fast, and an exception
after processing some items still exits nonzero. Existing manifests/state and recovery
procedures determine what was committed. The CLI does not resume or undo work merely
to format a result. Interrupted results include `recovery_hint`.

| Exit code | Meaning |
| --- | --- |
| 0 | Success; includes `review` catching Ctrl+C during serving as its existing normal shutdown |
| 1 | Configuration, processing or validation failure, including failure after some items were processed |
| 2 | Argument usage error |
| 130 | Keyboard interruption propagated by setup or processing |

Browser-launch warnings after a successful build keep exit 0 and remain on stderr;
the result still supplies the generated index path. An unrecoverable termination,
process kill or closed stdout can prevent delivery of a result.

