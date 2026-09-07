"""Source-backed interval aggregation for the local activity report."""

from __future__ import annotations

import base64
import bisect
import csv
import fnmatch
import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .export import decode_cell
from .report_history import pack_history
from .report_rules import extract

LOG = logging.getLogger(__name__)
GENERATOR_VERSION = "2.0.0"
APP_FIELDS = ("category", "family", "channel", "purpose")
CATEGORIES = {"Browser", "Desktop app", "System / Shell", "Unknown"}
RULE_FIELDS = {"device_id", "valid_from", "valid_to", "match_field", "pattern", *APP_FIELDS}
BROWSERS = {
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "vivaldi.exe": "Vivaldi",
    "opera.exe": "Opera",
    "iexplore.exe": "Internet Explorer",
}
SYSTEM = {
    "explorer.exe",
    "searchhost.exe",
    "searchapp.exe",
    "startmenuexperiencehost.exe",
    "shellexperiencehost.exe",
    "lockapp.exe",
    "logonui.exe",
    "taskmgr.exe",
    "systemsettings.exe",
    "control.exe",
    "mmc.exe",
    "consent.exe",
}
DESKTOP = {
    "code.exe": "VS Code",
    "excel.exe": "Excel",
    "winword.exe": "Word",
    "powerpnt.exe": "PowerPoint",
    "onenote.exe": "OneNote",
    "outlook.exe": "Outlook",
    "obsidian.exe": "Obsidian",
    "codex.exe": "Codex",
    "chatgpt.exe": "ChatGPT",
    "notepad.exe": "Notepad",
    "notepad++.exe": "Notepad++",
    "emeditor.exe": "EmEditor",
    "windowsterminal.exe": "Windows Terminal",
    "cmd.exe": "Command Prompt",
    "powershell.exe": "PowerShell",
    "pwsh.exe": "PowerShell",
    "mstsc.exe": "Remote Desktop",
    "slack.exe": "Slack",
    "teams.exe": "Teams",
    "ms-teams.exe": "Teams",
    "zoom.exe": "Zoom",
    "discord.exe": "Discord",
    "vlc.exe": "VLC",
    "manictime.exe": "ManicTime",
    "paintdotnet.exe": "Paint.NET",
    "mspaint.exe": "Paint",
    "acrobat.exe": "Acrobat",
    "acrord32.exe": "Acrobat Reader",
    "7zfm.exe": "7-Zip",
    "devenv.exe": "Visual Studio",
    "winmergeu.exe": "WinMerge",
}


def csv_rows(path: Path, fields: set[str] | None = None):
    """Decode the pipeline's NULL/backslash contract, including long window titles."""
    csv.field_size_limit(32 * 1024 * 1024)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for raw in csv.DictReader(stream):
            yield {k: decode_cell(v) for k, v in raw.items() if fields is None or k in fields}


def rules_from(path: Path | None, *, sites: bool = False) -> list[dict]:
    if path is None:
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        expected = {"host", "service", "purpose"} if sites else RULE_FIELDS
        if set(reader.fieldnames or []) != expected:
            raise ValueError(f"Rule CSV columns must be {sorted(expected)}: {path}")
        result = []
        for number, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"Invalid rule row {number}: {path}")
            row = {k: v.strip() for k, v in row.items()}
            if sites:
                if not row["host"] or not row["service"]:
                    raise ValueError(f"host and service are required: {path}:{number}")
                row["host"] = row["host"].lower().rstrip(".")
            else:
                if row["match_field"] not in {"key", "name", "title", "file_name"}:
                    raise ValueError(f"Unsupported match_field: {path}:{number}")
                if not row["pattern"] or not row["family"] or row["category"] not in CATEGORIES:
                    raise ValueError(f"Invalid application classification: {path}:{number}")
                for key in ("valid_from", "valid_to"):
                    if row[key]:
                        datetime.strptime(row[key], "%Y-%m-%d")
                if row["valid_from"] and row["valid_to"] and row["valid_from"] > row["valid_to"]:
                    raise ValueError(f"Rule dates are reversed: {path}:{number}")
            result.append(row)
        if sites and len({r["host"] for r in result}) != len(result):
            raise ValueError(f"Duplicate site hosts: {path}")
        return result


def classify(group: dict, title: str, device: str, day: str, rules: list[dict]) -> tuple:
    name = group.get("Name") or "Unknown"
    key = group.get("Key") or ""
    meta = json.loads(group.get("Other") or "{}")
    executable = (meta.get("fileName") or key.split(";")[0]).lower()
    context = {"name": name, "key": key, "title": title, "file_name": executable}
    for rule in rules:
        if rule["device_id"] and rule["device_id"] != device:
            continue
        if rule["valid_from"] and day < rule["valid_from"]:
            continue
        if rule["valid_to"] and day > rule["valid_to"]:
            continue
        if fnmatch.fnmatchcase(context[rule["match_field"]].casefold(), rule["pattern"].casefold()):
            return tuple(rule[k] or ("Unknown" if k == "channel" else "") for k in APP_FIELDS)
    if executable in BROWSERS:
        family = BROWSERS[executable]
        # Group metadata is shared across historical instances. Never infer a channel from its path.
        clean_title = title.replace("\u200b", "")
        match = re.search(
            r"(?:Chrome|Edge|Firefox)\s+(Beta|Dev|Canary|Nightly)\s*$", clean_title, re.I
        )
        channel = match[1].title() if match else "Unknown"
        return "Browser", family, channel, ""
    if executable in SYSTEM:
        return "System / Shell", name, "", ""
    if executable in DESKTOP:
        return "Desktop app", DESKTOP[executable], "", ""
    return "Unknown", name, "", ""


def interval(row: dict, cutoff: float, quality: Counter) -> tuple[float, float] | None:
    try:
        start_value = datetime.fromisoformat(row["StartUtcTime"])
        end_value = datetime.fromisoformat(row["EndUtcTime"])
        start = (start_value if start_value.tzinfo else start_value.replace(tzinfo=UTC)).timestamp()
        end = (end_value if end_value.tzinfo else end_value.replace(tzinfo=UTC)).timestamp()
    except (ValueError, TypeError, KeyError, OverflowError):
        quality["invalid_timestamp_rows"] += 1
        return None
    if end <= start:
        quality["nonpositive_duration_rows"] += 1
        return None
    if start >= cutoff:
        quality["excluded_today_or_future_rows"] += 1
        return None
    return start, min(end, cutoff)


def merge_intervals(intervals: list[tuple]) -> list[tuple]:
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(end, merged[-1][1])
        else:
            merged.append((start, end))
    return merged


class IntervalIndex:
    def __init__(self, intervals):
        self.intervals = merge_intervals(intervals)
        self.ends = [p[1] for p in self.intervals]

    def seconds(self, start, end):
        total = 0.0
        i = bisect.bisect_right(self.ends, start)
        while i < len(self.intervals) and self.intervals[i][0] < end:
            left, right = self.intervals[i]
            total += max(0, min(end, right) - max(start, left))
            i += 1
        return total


def pieces(start: float, end: float, tz: ZoneInfo):
    """Split elapsed UTC seconds at local hour/day boundaries, including DST transitions."""
    while start < end:
        local = datetime.fromtimestamp(start, tz)
        next_hour = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        stop = min(end, next_hour.timestamp())
        if stop <= start:
            stop = min(end, start + 3600)
        yield local.date().isoformat(), local.hour, start, stop
        start = stop


@lru_cache(maxsize=20000)
def period_ids(day: str):
    d = datetime.strptime(day, "%Y-%m-%d").date()
    iso = d.isocalendar()
    return "all", day[:4], day[:7], f"{iso.year}-W{iso.week:02}"


def lookup_term(url: str, title: str, rules: list[dict]) -> tuple | None:
    result = extract(url, title, rules, "dictionary")
    return tuple(result[k] for k in ("host", "term", "search_term", "method")) if result else None


def metric():
    return {"foreground_seconds": 0.0, "active_seconds": 0.0, "intervals": 0, "days": set()}


def bump(bucket: dict, key, day, seconds, active_seconds, count):
    value = bucket.setdefault(key, metric())
    value["foreground_seconds"] += seconds
    value["active_seconds"] += active_seconds
    value["intervals"] += count
    value["days"].add(day)


def build_data(
    profile,
    activity_paths: list[Path],
    tz: ZoneInfo,
    cutoff: float,
    app_rules: list[dict],
    site_rules: list[dict],
    gap_minutes: int,
    extraction: list[dict],
) -> dict:
    root = profile.processed_path
    timelines = list(csv_rows(root / "Ar_Timeline/all.csv", {"ReportId", "SchemaName"}))
    ids = {}
    for kind in ("ComputerUsage", "Applications", "Documents"):
        matches = [r["ReportId"] for r in timelines if r["SchemaName"] == f"ManicTime/{kind}"]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one {kind} timeline for {profile.device_id}; found {len(matches)}"
            )
        ids[kind] = matches[0]
    groups = {
        (r["ReportId"], r["GroupId"]): r
        for r in csv_rows(
            root / "Ar_Group/all.csv",
            {"ReportId", "GroupId", "Name", "Key", "Other", "Icon16", "Icon32"},
        )
    }
    icons, group_icons, app_icons = {}, {}, {}
    for key, group in groups.items():
        raw = group.get("Icon32") or group.get("Icon16")
        if isinstance(raw, bytes) and raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) <= 256_000:
            digest = hashlib.sha256(raw).hexdigest()
            icons[digest] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
            group_icons[key] = digest
    active_intervals, usage_intervals = [], []
    source_max_end = 0.0
    quality = Counter()
    fields = {
        "ReportId",
        "ActivityId",
        "Name",
        "GroupId",
        "StartUtcTime",
        "EndUtcTime",
        "RelatedActivityId",
    }
    for i, path in enumerate(activity_paths, 1):
        if i == 1 or i % 12 == 0 or i == len(activity_paths):
            LOG.info("%s: computer usage %d/%d", profile.device_id, i, len(activity_paths))
        for row in csv_rows(path, fields):
            try:
                end_value = datetime.fromisoformat(row["EndUtcTime"])
                source_max_end = max(
                    source_max_end,
                    (end_value if end_value.tzinfo else end_value.replace(tzinfo=UTC)).timestamp(),
                )
            except (ValueError, TypeError, KeyError, OverflowError):
                pass
            if row["ReportId"] != ids["ComputerUsage"]:
                continue
            span = interval(row, cutoff, quality)
            if span:
                usage_intervals.append(span)
                group = groups.get((row["ReportId"], row["GroupId"]), {})
                if group.get("Key") == "ManicTime/Active" or group.get("Name") == "Active":
                    active_intervals.append(span)
    active = IntervalIndex(active_intervals)
    available = IntervalIndex(usage_intervals)
    if not available.intervals:
        raise ValueError(f"No completed-day ComputerUsage records: {profile.device_id}")
    daily = defaultdict(
        lambda: {
            "pc_active_seconds": 0.0,
            "recorded_seconds": 0.0,
            "app_active_seconds": 0.0,
            "browser_active_seconds": 0.0,
            "web_active_seconds": 0.0,
            "hourly": [0.0] * 24,
        }
    )
    for start, end in available.intervals:
        for day, _hour, left, right in pieces(start, end, tz):
            daily[day]["recorded_seconds"] += right - left
    for start, end in active.intervals:
        for day, hour, left, right in pieces(start, end, tz):
            daily[day]["pc_active_seconds"] += right - left
            daily[day]["hourly"][hour] += right - left
    periods = defaultdict(lambda: {"apps": {}, "domains": {}})
    inventory = {}
    lookups, unresolved = [], []
    history, history_payloads = [], {}
    dictionary_hosts = {h for r in extraction if r["kind"] == "dictionary" for h in r["hosts"]}
    sites_by_host = {r["host"]: r for r in site_rules}
    carry = {}
    last_ends = {}
    first, last = available.intervals[0][0], available.intervals[-1][1]
    for i, path in enumerate(activity_paths, 1):
        LOG.info("%s: applications and websites %d/%d", profile.device_id, i, len(activity_paths))
        rows = list(csv_rows(path, fields))
        history_rows = []
        apps = dict(carry)
        app_rows = [r for r in rows if r["ReportId"] == ids["Applications"]]
        doc_rows = [r for r in rows if r["ReportId"] == ids["Documents"]]
        for kind, selected in (("apps", app_rows), ("domains", doc_rows)):
            # A timeline must not double-count simultaneous records. Reject ambiguity visibly.
            valid = []
            for row in selected:
                span = interval(row, cutoff, quality)
                if span:
                    valid.append((*span, row))
            for start, end, row in sorted(valid, key=lambda item: (item[0], item[1])):
                if start < last_ends.get(kind, float("-inf")):
                    raise ValueError(
                        f"Overlapping {kind} intervals in {path.name}; inspect source data"
                    )
                last_ends[kind] = end
                first, last = min(first, start), max(last, end)
                group = groups.get((row["ReportId"], row["GroupId"]), {})
                title = row.get("Name") or ""
                day = datetime.fromtimestamp(start, tz).date().isoformat()
                if kind == "apps":
                    classification = classify(group, title, profile.device_id, day, app_rules)
                    apps[row["ActivityId"]] = (start, end, classification, title)
                    key = classification
                    icon_key = group_icons.get((row["ReportId"], row["GroupId"]))
                    if icon_key:
                        app_icons.setdefault(classification, icon_key)
                    history_rows.append(
                        [
                            datetime.fromtimestamp(start, tz).isoformat(),
                            datetime.fromtimestamp(end, tz).isoformat(),
                            title,
                            classification[1],
                            row["ActivityId"],
                            active.seconds(start, end),
                            "title",
                            "",
                            row["ReportId"],
                        ]
                    )
                    invkey = (
                        group.get("Key") or "",
                        group.get("Name") or "Unknown",
                        *classification,
                    )
                    bump(inventory, invkey, day, end - start, active.seconds(start, end), 1)
                else:
                    try:
                        parsed = urlsplit(title)
                        host = (parsed.hostname or "").lower().rstrip(".")
                    except ValueError:
                        quality["invalid_url_rows"] += 1
                        continue
                    if parsed.scheme.lower() not in {"http", "https"} or not host:
                        continue
                    parent = apps.get(row.get("RelatedActivityId"))
                    if parent is None:
                        quality["web_rows_without_application"] += 1
                        classification = ("Unknown", "Unknown", "Unknown", "")
                    else:
                        classification = parent[2]
                    site = sites_by_host.get(host, {})
                    key = (host, site.get("service", host), site.get("purpose", ""))
                    normalized_host = host.removeprefix("www.")
                    search = extract(title, parent[3] if parent else "", extraction, "search")
                    if search:
                        history_rows.append(
                            [
                                datetime.fromtimestamp(start, tz).isoformat(),
                                datetime.fromtimestamp(end, tz).isoformat(),
                                search["term"],
                                classification[1],
                                row["ActivityId"],
                                active.seconds(start, end),
                                "search",
                                title,
                                row["ReportId"],
                            ]
                        )
                    if normalized_host in dictionary_hosts:
                        app_title = parent[3] if parent else ""
                        word = lookup_term(title, app_title, extraction)
                        evidence = {
                            "source_file": path.relative_to(root).as_posix(),
                            "report_id": row["ReportId"],
                            "activity_id": row["ActivityId"],
                            "day": day,
                            "start_utc": datetime.fromtimestamp(start, UTC).isoformat(),
                            "end_utc": datetime.fromtimestamp(end, UTC).isoformat(),
                            "url": title,
                            "title": app_title,
                        }
                        if word:
                            lookups.append(
                                {
                                    **evidence,
                                    "host": word[0],
                                    "term": word[1],
                                    "search_term": word[2],
                                    "method": word[3],
                                }
                            )
                        else:
                            unresolved.append(evidence)
                seen = set()
                for part_day, _hour, left, right in pieces(start, end, tz):
                    seconds, active_seconds = right - left, active.seconds(left, right)
                    if kind == "apps":
                        daily[part_day]["app_active_seconds"] += active_seconds
                        if classification[0] == "Browser":
                            daily[part_day]["browser_active_seconds"] += active_seconds
                    elif parent and classification[0] == "Browser":
                        # Coverage counts only the URL/application intersection.
                        a, b = max(left, parent[0]), min(right, parent[1])
                        if a < b:
                            daily[part_day]["web_active_seconds"] += active.seconds(a, b)
                    for pid in period_ids(part_day):
                        bump(
                            periods[pid][kind],
                            key,
                            part_day,
                            seconds,
                            active_seconds,
                            int(pid not in seen),
                        )
                        seen.add(pid)
        next_month = (
            datetime(int(path.parent.name), int(path.stem), 28) + timedelta(days=4)
        ).replace(day=1)
        # CSV partitioning follows recorded StartLocalTime, not necessarily report_timezone.
        # Keep intervals near the boundary, including a one-day timezone margin.
        boundary = next_month.replace(tzinfo=tz).timestamp() - 86400
        if history_rows:
            relative = (
                "history/"
                + path.relative_to(root)
                .as_posix()
                .removeprefix("Ar_Activity/")
                .replace("/", "-")
                .removesuffix(".csv")
                + ".js"
            )
            history_payloads[relative] = pack_history(
                history_rows, path.relative_to(root).as_posix()
            )
            history.append(
                {
                    "path": relative,
                    "first": min(r[0][:10] for r in history_rows),
                    "last": max(r[1][:10] for r in history_rows),
                    "rows": len(history_rows),
                    "titles": sum(r[6] == "title" for r in history_rows),
                    "searches": sum(r[6] == "search" for r in history_rows),
                }
            )
        carry = {k: v for k, v in apps.items() if v[1] >= boundary}
    lookup_last = {}
    for event in sorted(lookups, key=lambda r: (r["start_utc"], r["activity_id"])):
        start = datetime.fromisoformat(event["start_utc"]).timestamp()
        end = datetime.fromisoformat(event["end_utc"]).timestamp()
        prior = lookup_last.get(event["term"])
        new_session = not prior or prior[0] != event["day"] or start - prior[1] > gap_minutes * 60
        sid = f"{event['report_id']}:{event['activity_id']}" if new_session else prior[2]
        event["session_id"] = sid
        lookup_last[event["term"]] = event["day"], end, sid
    for day in daily:
        for pid in period_ids(day):
            periods[pid]  # Include recorded days without application activity.
    for pid, period in periods.items():
        selected_days = [day for day in sorted(daily) if pid in period_ids(day)]
        period["days"] = selected_days
        for kind, names in (("apps", APP_FIELDS), ("domains", ("host", "service", "purpose"))):
            period[kind] = [
                {**dict(zip(names, key, strict=True)), **value, "days": sorted(value["days"])}
                for key, value in period[kind].items()
            ]
            period[kind].sort(key=lambda r: (-r["active_seconds"], -r["foreground_seconds"]))
        terms = {}
        for event in lookups:
            if pid not in period_ids(event["day"]):
                continue
            entry = terms.setdefault(
                event["term"],
                {
                    "term": event["term"],
                    "days": set(),
                    "sessions": set(),
                    "sites": set(),
                    "first": event["day"],
                    "last": event["day"],
                    "intervals": 0,
                },
            )
            entry["days"].add(event["day"])
            entry["sessions"].add(event["session_id"])
            entry["sites"].add(event["host"])
            entry["first"] = min(entry["first"], event["day"])
            entry["last"] = max(entry["last"], event["day"])
            entry["intervals"] += 1
        period["terms"] = sorted(
            [
                {
                    **v,
                    "days": len(v["days"]),
                    "sessions": len(v["sessions"]),
                    "sites": sorted(v["sites"]),
                }
                for v in terms.values()
            ],
            key=lambda r: (-r["days"], -r["sessions"], r["term"]),
        )
    for period in periods.values():
        for row in period["apps"]:
            row["icon"] = app_icons.get(tuple(row[k] for k in APP_FIELDS), "")
    for value in inventory.values():
        value["days"] = len(value["days"])
    return {
        "device_id": profile.device_id,
        "profile": profile.name,
        "source_max_end": source_max_end,
        "icons": icons,
        "history": history,
        "_history_payloads": history_payloads,
        "timezone": str(tz),
        "first": datetime.fromtimestamp(first, tz).isoformat(),
        "last": datetime.fromtimestamp(last, tz).isoformat(),
        "last_day": datetime.fromtimestamp(last - 0.000001, tz).date().isoformat(),
        "cutoff": datetime.fromtimestamp(cutoff, tz).isoformat(),
        "lookup_gap_minutes": gap_minutes,
        "periods": dict(periods),
        "daily": dict(sorted(daily.items())),
        "lookups": lookups,
        "unresolved_lookups": unresolved,
        "quality": dict(quality),
        "inventory": [
            {**dict(zip(("key", "name", *APP_FIELDS), key, strict=True)), **value}
            for key, value in sorted(inventory.items(), key=lambda p: -p[1]["active_seconds"])
        ],
    }


def report_cutoff(tz: ZoneInfo, now: datetime | None = None) -> float:
    local = (now or datetime.now(UTC)).astimezone(tz)
    return datetime.combine(local.date(), time.min, tzinfo=tz).timestamp()
