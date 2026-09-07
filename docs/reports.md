# PC activity reports

`build-report` reads ingested CSV and creates a local HTML report for each PC.
Switch between all history, years, months and weeks within a page to inspect applications,
websites, weekdays/hours and dictionary lookups. No network, CDN, AI, web server or Node.js is required.

## Configuration and use

Add these top-level settings to `~/.tkn/manictime_data_pipeline/config.yaml`:

```yaml
schema_version: "2.2.0"
report_path: ~/.tkn/manictime_data_pipeline/reports
report_timezone: Asia/Tokyo
report_lookup_gap_minutes: 30
# Optional user-maintained mapping CSVs:
# application_rules_path: C:/path/to/application_rules.csv
# site_rules_path: C:/path/to/site_rules.csv
```

The default `reports` directory separates human-facing outputs from Raw/CSV under `data`.
The CLI puts `index.html` directly in `report_path` and adds PC subdirectories under `devices`.
Do not include a PC name in the configured path. The report root must not overlap input,
Raw, extracted CSV or state, in either direction. Other ordinary files may coexist, but an
unmanaged `index.html` is never overwritten.

```console
tkn-manictime-pipeline build-report --dry-run
tkn-manictime-pipeline build-report
```

Normal execution writes reports and opens the finished index in the OS default browser.
`--no-open` suppresses only the browser launch. Browser failure keeps the generated files and
prints their path. Progress and result paths go to stderr; this command emits no JSON to stdout.

By default, **all configured profiles** are included, irrespective of `default_profile`.
This differs from the single-profile `ingest` command. `--profile NAME` updates one PC and
preserves links to other previously generated PCs. Every selected PC needs a successful ingest;
missing source state is an error rather than an implicit omission.

```console
tkn-manictime-pipeline build-report --profile current-pc --no-open
```

## Weekly operation

Run the appropriate PC's `ingest` successfully before running:

```console
tkn-manictime-pipeline build-report --no-open
```

The report command does not ingest. For Windows Task Scheduler, use the absolute installed
`tkn-manictime-pipeline` executable with these arguments. Use absolute data paths and, when
needed, `--config C:/path/to/config.yaml`. The CLI does not register a schedule.

No manual period selection is needed: all saved history is considered. CSV, classification,
day-cutoff or generator changes trigger regeneration. Identical inputs with intact outputs are
reused. Today is excluded at midnight in `report_timezone`; it becomes eligible on a later run.
Weeks are Monday–Sunday ISO weeks; months and years follow the calendar. Ongoing periods and
periods at the edges of the source range remain visible and are identified as partial.

## Time and classifications

- **Foreground time:** elapsed seconds in Applications/Documents intervals.
- **Foreground time while Active:** intersection with ComputerUsage Active intervals; the default ranking.
- **Website coverage:** identified URL time intersecting its related application, divided by browser foreground time while Active.
- **Viewing intervals:** source URL records overlapping the selected period, not requests, page loads or searches.
- **Weekday/hour heatmap:** mean Active minutes per recorded day of that weekday.

Elapsed time comes from UTC timestamps and is split at the chosen timezone's hour/day boundaries.
Cross-midnight records are divided, and ISO week-years are distinguished from calendar years.
Application, website and PC time describe overlapping perspectives and must not be added together.
Missing records leave chart gaps; they are not treated as zero usage. Reading/viewing may involve
little input, so these metrics do not evaluate productivity, outcomes or actual work time.

Applications use `Browser`, `Desktop app`, `System / Shell` or `Unknown`. Common executables have
built-in mappings; everything else remains unclassified. Current group executable paths are
never used to assign a historical Chrome/Edge channel. Only explicit Beta/Dev/etc. title suffixes
or user rules identify channels. Family/category aggregation unions dates instead of counting
the same day again for each channel.

## Supplemental CSVs

Examples are packaged at `src/manictime_pipeline/resources/application_rules.example.csv` and
`site_rules.example.csv`. Copy and edit them in a user-owned location, outside the report root
and extracted CSV directories. Point the optional settings to those copies. The generated
`application_inventory.csv` helps prioritize frequently used, unclassified applications.

Application rule columns:

```text
device_id,valid_from,valid_to,match_field,pattern,category,family,channel,purpose
```

The first matching row wins, ahead of built-in mappings. `match_field` is `key`, `name`, `title`
or `file_name`. `pattern` is a case-insensitive glob supporting `*`. Blank device/date fields
mean unrestricted; dates are inclusive YYYY-MM-DD values compared with the activity's local
start date. `category` and `family` are required; `channel` and `purpose` are optional.
A mapping cannot recover channel evidence that was never recorded.

Site columns are `host,service,purpose`. Hosts are lowercased and matched exactly; duplicates
are rejected. Subdomains are not silently combined. Service names and purposes appear in the
site detail table.

## Dictionary lookups

Weblio English/Japanese, Cambridge and Oxford Learner's Dictionaries URL paths identify headwords.
Known title formats provide a fallback. Search spelling, headword, URL, related window title,
source CSV, timeline ID and activity ID are preserved. Titles come from the related application ID.
Normalization consists of Unicode normalization, lowercasing and removal of Oxford sense numbers;
there is no automatic inflection merging or inferred meaning.

Consecutive views of the same term on the same day within 30 minutes form one session, including
views across dictionaries. Configure `report_lookup_gap_minutes` from 1 to 240. Days, sessions
and viewing intervals are distinct and are assigned by viewing start date. Unparsed dictionary
records remain in a separate CSV. The HTML evidence table displays at most 500 rows; CSV/JSON
retain all records.

## Outputs and publication

```text
<report_path>/
  index.html
  report-manifest.json
  devices/<device_id>/<fingerprint>/
    index.html
    data.json
    daily.csv
    applications.csv
    domains.csv
    dictionary_events.csv
    unresolved_dictionary.csv
    application_inventory.csv
    manifest.json
```

Each PC's HTML embeds all period aggregates and its reader. Supporting CSVs use seconds,
UTF-8 with BOM and semicolon-separated list cells. A leading apostrophe protects text that
spreadsheets could interpret as a formula; original strings remain in JSON. Filter application
and domain CSVs by `period` before aggregating: adding multiple time grains double-counts activity.

Inputs are checked against the successful ingest manifest and every referenced CSV hash. Reports
can read byte-identical relocated exports with the same device ID even if the ingest profile name
or paths have changed. The ingest command retains its stricter identity checks.
Normal report runs share ingest locks and reject pending recovery. Dry runs create no lock files
and recheck source checkpoints and CSV hashes afterward. Overlapping intervals on one timeline
are rejected; invalid timestamps, nonpositive durations and excluded current-day rows are counted
in quality metadata.

The index changes only after the selected PC generations are complete. The latest and prior
generation are retained. Older generations are removed only by verifying and deleting their
manifest-listed files. Edited or additional files preserve an old generation. Source data, Raw
and ingest records are not changed. Retry the same command after interruption; incomplete
generations are not linked from the index. Edited files in the same generation stop overwrite.
Make desired changes through classification CSVs rather than editing generated reports.

Itadaki event integration is a subsequent phase. This report does not infer input recipients or productivity.
