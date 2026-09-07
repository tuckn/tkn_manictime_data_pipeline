# PC activity reports

`build-report` reads ingested CSV and creates one local HTML entry page.
Choose all PCs or a profile next to the year/month/week filters. Inspect applications with
ManicTime icons, websites, weekdays/hours and dictionary lookups. A separate page searches
window titles and lists Google/Bing search terms. No network, CDN, AI, web server or Node.js is required.

## Configuration and use

Add these top-level settings to `~/.tkn/manictime_data_pipeline/config.yaml`:

```yaml
schema_version: "2.3.0"
report_path: ~/.tkn/manictime_data_pipeline/reports
report_timezone: Asia/Tokyo
report_lookup_gap_minutes: 30
# These defaults normally need no overrides. Create the files with rules init.
# application_rules_path: ~/.tkn/manictime_data_pipeline/rules/application_rules.csv
# site_rules_path: ~/.tkn/manictime_data_pipeline/rules/site_rules.csv
# extraction_rules_path: ~/.tkn/manictime_data_pipeline/rules/extraction_rules.yaml
```

The default `reports` directory separates human-facing outputs from Raw/CSV under `data`.
The CLI puts `index.html` directly in `report_path` and adds PC subdirectories under `devices`.
Do not include a PC name in the configured path. The report root must not overlap input,
Raw, extracted CSV or state, in either direction. Other ordinary files may coexist, but an
unmanaged `index.html` is never overwritten.

```console
tkn-manictime-pipeline rules init
tkn-manictime-pipeline build-report --all --dry-run
tkn-manictime-pipeline build-report --all
```

Normal execution writes reports and opens the finished index in the OS default browser.
`--no-open` suppresses only the browser launch. Browser failure keeps the generated files and
prints their path. Progress and result paths go to stderr; this command emits no JSON to stdout.

By default, only **default_profile** is updated, matching `ingest`. `--profile NAME` updates
one named PC; `--all` updates every configured profile. `--profile` and `--all` are mutually
exclusive. Selected profiles must have a successful ingest. Unselected PCs remain visible
using their previously generated data; the page shows each PC's last record and input capture.
Unbuilt profiles are listed as missing. Removed profiles with existing reports stay visible.

```console
tkn-manictime-pipeline build-report --profile current-pc --no-open
tkn-manictime-pipeline build-report --all --no-open
```

The PC filter supports all PCs and each individual PC. Combined durations are **the sum of
PC durations**, including simultaneous use; they are not the person's elapsed hours. Recorded
days and term lookup days use distinct calendar dates. Lookup sessions stay separate per PC.
The heatmap averages summed PC minutes over recorded calendar days. When a second active PC is
added, ingest each active profile and use `build-report --all`. Classification or extraction rule
changes should also be followed by `--all`; the page warns if cached PCs used different rules.
Reports with different timezones cannot be combined until regenerated with `--all`.

## Weekly operation

Run the appropriate PC's `ingest` successfully before running:

```console
tkn-manictime-pipeline build-report --no-open
```

The report command does not ingest. For Windows Task Scheduler, use the absolute installed
`tkn-manictime-pipeline` executable with these arguments. Use absolute data paths and, when
needed, `--config C:/path/to/config.yaml`. The CLI does not register a schedule.

No manual period selection is needed: all saved history is considered. CSV, classification,
eligible-day cutoff or aggregation-code changes trigger aggregation. The cutoff stops at the
latest source end time, so a retired PC is **not reaggregated just because the date changed**.
Template/publication-only changes reuse validated aggregates and history files. Missing output
files can be rebuilt; modified files are preserved with an error. Reuse still checks CSV/output
checksums and reads aggregate data to compose the combined page, so it requires some disk I/O
and CPU. It does not rescan activities for aggregation when unchanged. Today is excluded at midnight in `report_timezone`; it becomes eligible on a later run.
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

## Editable rules: setup

Run `tkn-manictime-pipeline rules init` once. It copies packaged defaults to:

```text
~/.tkn/manictime_data_pipeline/rules/
  application_rules.csv
  site_rules.csv
  extraction_rules.yaml
```

Existing files are preserved, including edits. `rules init --dry-run` lists actions without
writing. The three optional path settings override these locations; no path settings are needed
for normal use. `build-report` always reads all three files, even on a cache hit. Missing or
invalid files stop the command; `rules init` restores missing files only. For an empty rule set,
keep the CSV header or use `rules: []` in the extraction YAML. Package upgrades do not overwrite
user files. Compare updated package examples and merge desired changes yourself.

Editable files belong outside generated reports and source/Raw/CSV/state directories. They are
shared by profiles, with optional device/date restrictions for application rules. They are not
copied into the per-PC output directories. These are ordinary UTF-8 files (BOM accepted), not
the ingest CSV escaping format. Quote CSV cells containing commas, quotes or line breaks.

### application_rules.csv

Use `application_inventory.csv` to inspect actual `key` and `name` values, then add a row.
The first matching row wins over built-in executable mappings. Put narrow rules above broad rules.

| Column | Meaning / example |
| --- | --- |
| `device_id` | Exact configured device ID, **not profile name**; blank applies to all PCs. |
| `valid_from`, `valid_to` | Inclusive local start dates, `YYYY-MM-DD`; blank is unbounded. |
| `match_field` | `key` = Ar_Group.Key; `name` = Ar_Group.Name; `title` = application window title; `file_name` = metadata executable name, falling back to the prefix of Key. |
| `pattern` | Case-insensitive glob: `*` any text, `?` one character, `[abc]` a character set. Not a regular expression. |
| `category` | Required: `Browser`, `Desktop app`, `System / Shell`, or `Unknown`. |
| `family` | Required grouping label, for example `Chrome` or `VS Code`. |
| `channel` | Optional subcategory such as `Dev`, `Beta`, `Stable`; blank becomes `Unknown` for a matched rule. |
| `purpose` | Optional personal label. Saved in CSV/JSON; currently no purpose filter in the HTML. |

```csv
device_id,valid_from,valid_to,match_field,pattern,category,family,channel,purpose
,,,title,*Google Chrome Dev,Browser,Chrome,Dev,Research
,,,file_name,code.exe,Desktop app,VS Code,,Development
```

The first row classifies titles ending in Google Chrome Dev. The second classifies the VS Code
executable. Neither guesses a browser channel from today's installation path. A date/device
rule is useful only when you have independent evidence that the mapping held throughout that range.
ManicTime's PNG icons are deduplicated and embedded locally; the HTML displays them next to
application names. Missing icons leave an empty space; icons never determine classification.

### site_rules.csv

| Column | Meaning / example |
| --- | --- |
| `host` | Required exact lowercased host, for example `ejje.weblio.jp`; no URL, path, wildcard or implicit subdomain merge. Trailing dots are ignored; `www.` remains distinct here. |
| `service` | Required display name such as `Weblio`. |
| `purpose` | Optional label shown in site details, for example `Dictionary`. |

```csv
host,service,purpose
ejje.weblio.jp,Weblio,Dictionary
```

Duplicate hosts are errors. This CSV labels sites; it does **not** enable dictionary extraction
or merge domain rankings by service. Unlisted hosts still appear in rankings using their host
names. Dictionary/search extraction is controlled by the YAML below.

### extraction_rules.yaml

This file is mandatory for `build-report`. It moves the former built-in dictionary patterns
into editable data and also supplies Google/Bing query extraction. It is a rule file, separate
from general `config.yaml`. `schema_version: "1.0.0"` and a `rules` list are required.

| Field | Meaning |
| --- | --- |
| `id` | Unique nonempty rule name; also identifies an extraction rule in diagnostics. |
| `kind` | `dictionary` or `search`. |
| `hosts` | List of exact lowercase host names. Only here, leading `www.` is removed before matching. Add regional Google hosts explicitly if needed. |
| `path_pattern` | Case-insensitive Python regex over the percent-decoded URL path. Dictionary rules require a first capture group for the headword; search rules use it to restrict valid result pages. |
| `title_pattern` | Optional dictionary fallback regex over the related application title; first capture group is the headword. |
| `query_parameter` | Query-string key. Required for search (`q` for Google/Bing); dictionary defaults to `q` and records the searched spelling. |
| `strip_suffix` | Optional regex removed from the extracted dictionary term, e.g. `'_\d+$'` removes Oxford sense numbers. |

```yaml
schema_version: "1.0.0"
rules:
  - id: weblio
    kind: dictionary
    hosts: [ejje.weblio.jp]
    path_pattern: '^/content/([^/]+)'
    title_pattern: '^(.+?)の意味(?:・使い方)?'
  - id: google
    kind: search
    hosts: [google.com, google.co.jp]
    path_pattern: '^/search/?$'
    query_parameter: q
```

Use single-quoted YAML strings for regex backslashes. The first successful rule within a kind
wins. Search rules require a matching path and nonempty query; title-only search inference is
not used. The defaults cover Weblio, Cambridge, Oxford, Google (.com/.co.jp) and Bing. A custom
host or format can be added without editing Python. Use `--all --dry-run` to validate edits,
then `--all --no-open` to apply them. The dry run parses real data but does not publish it.

## Dictionary lookups

Weblio English/Japanese, Cambridge and Oxford Learner's Dictionaries URL paths identify headwords.
Known title formats provide a fallback. Search spelling, headword, URL, related window title,
source CSV, timeline ID and activity ID are preserved. Titles come from the related application ID.
Normalization consists of Unicode normalization, lowercasing and removal of Oxford sense numbers;
there is no automatic inflection merging or inferred meaning.

Consecutive views on the same PC of the same term on the same day within 30 minutes form one session, including
views across dictionaries. Configure `report_lookup_gap_minutes` from 1 to 240. Days, sessions
and viewing intervals are distinct and are assigned by viewing start date. Unparsed dictionary
records remain in a separate CSV. The HTML evidence table displays at most 500 rows; CSV/JSON
retain all records.

## Title and search history

Open the history links from the main report. PC and date filters carry over. A direct opening
starts at the last 30 recorded days; choose All dates to search older records. Choose window
titles or search terms, enter a substring, and press Search. Matching ignores letter case and
normalizes fullwidth/halfwidth characters. Results show local start/end, PC, application, text,
Active seconds and source CSV/timeline/activity IDs. Search rows include the original URL.

History is stored in month-sized JavaScript data files, using dictionaries for repeated strings.
The separate page loads only chunks overlapping the selected dates, sequentially, without a
web server or external requests. It reports total matches and first/last occurrence, and displays
the newest 1,000 matches in pages of 50. Narrow the dates to inspect older results beyond that
limit. Cancel stops after the current file load. Failed loads and partial results are marked.
The date filter uses the interval's start date. A missing browser URL cannot yield a search term.

These are recorded foreground/URL intervals, not browser history imports, search submission
counts, proof of watching a complete video, or the exact moment you first learned about it.
Identical query URLs may recur as you return to a results page. Full window-title history is
kept out of the main HTML and `data.json` to keep the dashboard responsive.

## Outputs and publication

```text
<report_path>/
  index.html
  report-manifest.json
  history-<hash>.html
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
    history/YYYY-MM.js
```

The root HTML embeds all available PC aggregates and their reader. Per-PC generations remain
as reusable caches and CSV downloads. The history page embeds only a small file catalog.
Supporting CSVs use seconds,
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

`<fingerprint>` is the **SHA-256 value calculated from input CSV, classification/extraction
rules, aggregation conditions, generation code and HTML templates**. It identifies a generation,
not a PC ID or a random run number. Formatting, renderer or rule changes can therefore produce
similar-looking generations. The data signature is tracked separately to reuse aggregates for
presentation-only changes.

The index changes only after the selected PC generations are complete. The latest and prior
generation are retained. The current and prior history entry pages are also retained. Older generations are removed only by verifying and deleting their
manifest-listed files. Edited or additional files preserve an old generation. Source data, Raw
and ingest records are not changed. Retry the same command after interruption; incomplete
generations are not linked from the index. Edited files in the same generation stop overwrite.
Make desired changes through classification CSVs rather than editing generated reports.

Itadaki event integration is a subsequent phase. This report does not infer input recipients or productivity.

## Why JSON examples are in docs

[`current.example.json`](current.example.json) illustrates the ingest checkpoint pointer;
[`manifest.example.json`](manifest.example.json) illustrates an abbreviated ingest run manifest.
They are synthetic documentation examples with placeholder hashes, linked from the README.
They are neither report manifests nor files read by the application. Keeping them in `docs`
lets readers understand persisted formats. Runtime templates and initial rule/config files
belong in `src/manictime_pipeline/resources` because installed commands read those resources.
