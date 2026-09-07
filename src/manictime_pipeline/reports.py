"""Validated CSV inputs, immutable report generations and atomic HTML entry points."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import uuid
from contextlib import ExitStack, nullcontext
from datetime import datetime, time, timedelta
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import transaction
from .config import path_value, selected_profile
from .io import child_path, process_lock, read_json, sha256_file
from .report_data import GENERATOR_VERSION, build_data, report_cutoff, rules_from
from .report_rules import extraction_rules, rule_paths
from .validation import validate_artifacts, validate_csv_contract

LOG = logging.getLogger(__name__)
REPORT_FORMAT = "1.0.0"
REPORT_FILES = {
    "index.html",
    "daily.csv",
    "applications.csv",
    "domains.csv",
    "dictionary_events.csv",
    "unresolved_dictionary.csv",
    "application_inventory.csv",
    "data.json",
}


def load_report_source(profile) -> dict | None:
    """Verify content identity without requiring the original ingest paths/profile name.

    A read-only report may consume a relocated, byte-identical export. Ingest keeps
    its stricter dataset identity contract; no checkpoint is rewritten here.
    """
    pointer = profile.state_path / "current.json"
    if not pointer.exists():
        return None
    current = read_json(pointer)
    if current.get("schema_version") != "3.0.0":
        raise ValueError("Unsupported source checkpoint schema")
    path = child_path(profile.state_path, current["manifest"])
    if sha256_file(path) != current["sha256"]:
        raise ValueError(f"Source manifest checksum mismatch: {path}")
    source = read_json(path)
    if source.get("schema_version") != "3.0.0" or source.get("device_id") != profile.device_id:
        raise ValueError("Source manifest device/schema mismatch")
    if current["manifest"] != f"runs/{source['run_id']}.json":
        raise ValueError("Source checkpoint manifest path mismatch")
    validate_csv_contract(source)
    for key, artifact in source["artifacts"].items():
        if artifact["path"] != f"{key}.csv":
            raise ValueError("Unexpected source artifact path")
        child_path(profile.processed_path, artifact["path"])
    return source


def canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A source-aligned mart path plus a full SHA-256 directory is already long on
    # Windows. Do not append the target name to a temporary filename as well.
    temporary = path.with_name(f".{uuid.uuid4().hex[:12]}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    atomic_bytes(path, payload.encode("utf-8"))


def csv_bytes(rows: list[dict], columns: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, columns, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        safe = {}
        for key, value in row.items():
            if isinstance(value, list):
                value = ";".join(str(v) for v in value)
            # Spreadsheet formulas must never be executed from titles or untrusted names.
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            safe[key] = value
        writer.writerow(safe)
    return stream.getvalue().encode("utf-8-sig")


def render_report(data: dict, resource="report.html") -> bytes:
    template = files("manictime_pipeline").joinpath("resources", resource).read_text("utf-8")
    payload = canonical(data).decode("utf-8").replace("&", "\\u0026").replace("<", "\\u003c")
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return template.replace("__REPORT_DATA__", payload).encode("utf-8")


def output_payloads(data: dict) -> dict[str, bytes]:
    daily = [{"day": day, **row} for day, row in data["daily"].items()]
    apps, domains = [], []
    for pid, period in sorted(data["periods"].items()):
        apps.extend({"period": pid, **row} for row in period["apps"])
        domains.extend({"period": pid, **row} for row in period["domains"])
    evidence = [
        "source_file",
        "report_id",
        "activity_id",
        "day",
        "start_utc",
        "end_utc",
        "url",
        "title",
    ]
    return {
        "index.html": render_report(data),
        "data.json": canonical(data),
        "daily.csv": csv_bytes(
            daily,
            [
                "day",
                "pc_active_seconds",
                "recorded_seconds",
                "app_active_seconds",
                "browser_active_seconds",
                "web_active_seconds",
                "hourly",
            ],
        ),
        "applications.csv": csv_bytes(
            apps,
            [
                "period",
                "category",
                "family",
                "channel",
                "purpose",
                "active_seconds",
                "foreground_seconds",
                "intervals",
                "days",
            ],
        ),
        "domains.csv": csv_bytes(
            domains,
            [
                "period",
                "host",
                "service",
                "purpose",
                "active_seconds",
                "foreground_seconds",
                "intervals",
                "days",
            ],
        ),
        "dictionary_events.csv": csv_bytes(
            data["lookups"], [*evidence, "host", "term", "search_term", "method", "session_id"]
        ),
        "unresolved_dictionary.csv": csv_bytes(data["unresolved_lookups"], evidence),
        "application_inventory.csv": csv_bytes(
            data["inventory"],
            [
                "key",
                "name",
                "category",
                "family",
                "channel",
                "purpose",
                "active_seconds",
                "foreground_seconds",
                "intervals",
                "days",
            ],
        ),
    }


def root_manifest(root: Path) -> dict:
    path = root / "report-manifest.json"
    if not path.exists():
        if (root / "index.html").exists():
            raise ValueError(f"Unmanaged index.html exists; choose another report_path: {root}")
        return {"schema_version": REPORT_FORMAT, "devices": {}}
    current = read_json(path)
    if current.get("schema_version") != REPORT_FORMAT or not isinstance(
        current.get("devices"), dict
    ):
        raise ValueError(f"Unsupported report manifest: {path}")
    if (root / "index.html").exists() and sha256_file(root / "index.html") != current[
        "index_sha256"
    ]:
        pending = root / ".index.pending.html"
        interrupted = (
            pending.is_file()
            and sha256_file(pending) == current["index_sha256"]
            and sha256_file(root / "index.html") == current.get("previous_index_sha256")
        )
        if not interrupted:
            raise ValueError(f"Edited report index is preserved: {root / 'index.html'}")
    return current


def validate_generation(folder: Path, device: str, fingerprint: str) -> dict | None:
    path = folder / "manifest.json"
    if not path.exists():
        return None
    record = read_json(path)
    if record.get("device_id") != device or record.get("fingerprint") != fingerprint:
        raise ValueError(f"Report generation identity mismatch: {folder}")
    names = set(record["outputs"])
    if not REPORT_FILES <= names or any(
        not re.fullmatch(r"history/\d{4}-\d{2}\.js", name) for name in names - REPORT_FILES
    ):
        raise ValueError(f"Unexpected managed report files: {folder}")
    missing = False
    for relative, expected in record["outputs"].items():
        artifact = child_path(folder, relative)
        if not artifact.exists():
            missing = True
        elif sha256_file(artifact) != expected:
            raise ValueError(f"Edited generated report is preserved: {artifact}")
    return None if missing else record


def combined_payload(root: Path, devices: dict, profiles: dict) -> dict:
    datasets = []
    for device, entry in sorted(devices.items()):
        folder = child_path(root, entry["path"])
        if validate_generation(folder, device, entry["fingerprint"]) is None:
            raise ValueError(f"Incomplete report for {device}; rebuild that profile")
        data = read_json(folder / "data.json")
        data["report_base"] = "/".join(quote(p) for p in Path(entry["path"]).parts) + "/"
        data["profile"] = next(
            (name for name, p in profiles.items() if p["device_id"] == device),
            data.get("profile", device),
        )
        datasets.append(data)
    if len({d["timezone"] for d in datasets}) > 1:
        raise ValueError("Different report timezones; use build-report --all to align them")
    return {
        "profiles": datasets,
        "missing_profiles": sorted(
            name for name, p in profiles.items() if p["device_id"] not in devices
        ),
    }


def history_page(payload: dict) -> bytes:
    catalog = [
        {
            "device_id": d["device_id"],
            "profile": d["profile"],
            "timezone": d["timezone"],
            "base": d["report_base"],
            "chunks": d.get("history", []),
        }
        for d in payload["profiles"]
    ]
    return render_report({"profiles": catalog}, "history.html")


def validate_report_paths(root: Path, profiles: list, rule_paths: list) -> None:
    for profile in profiles:
        for other in (
            profile.source_path,
            profile.raw_directory,
            profile.processed_path,
            profile.state_path,
        ):
            other = other.resolve()
            if root == other or root.is_relative_to(other) or other.is_relative_to(root):
                raise ValueError(f"report_path overlaps source/Raw/CSV/state: {root} and {other}")
    for path in rule_paths:
        if path and (path == root or path.is_relative_to(root)):
            raise ValueError("Keep editable rule files outside report_path")
        for profile in profiles:
            if path and any(
                path.is_relative_to(other.resolve())
                for other in (
                    profile.source_path,
                    profile.raw_directory,
                    profile.processed_path,
                    profile.state_path,
                )
            ):
                raise ValueError("Keep editable rules outside source/Raw/CSV/state")


def clean_old_generation(root: Path, entry: dict) -> None:
    """Remove only verified, manifest-listed files in one exact generated directory."""
    folder = child_path(root, entry["path"])
    if folder.parent.parent != root / "devices" or folder.name != entry["fingerprint"]:
        raise ValueError("Unexpected old generation path")
    try:
        record = validate_generation(folder, entry["device_id"], entry["fingerprint"])
        if record is None:
            return
        managed = set(record["outputs"]) | {"manifest.json"}
        contents = list(folder.rglob("*"))
        expected_dirs = {Path(name).parent.as_posix() for name in managed} - {"."}
        actual_dirs = {p.relative_to(folder).as_posix() for p in contents if p.is_dir()}
        if (
            {p.relative_to(folder).as_posix() for p in contents if p.is_file()} != managed
            or actual_dirs != expected_dirs
            or any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in contents)
        ):
            LOG.warning("Extra files in old report; preserving %s", folder)
            return
        for name in managed:
            child_path(folder, name).unlink()
        for relative in sorted(expected_dirs, reverse=True):
            child_path(folder, relative).rmdir()
        folder.rmdir()
    except (ValueError, OSError) as exc:
        LOG.warning("Old report preserved: %s", exc)


def build_report(
    config: dict, dry_run: bool = False, profile_name: str | None = None, all_profiles: bool = False
) -> dict:
    values = config["values"]
    if profile_name and all_profiles:
        raise ValueError("--profile and --all cannot be combined")
    root = path_value(values["report_path"])
    try:
        tz = ZoneInfo(values["report_timezone"])
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown report_timezone: {values['report_timezone']}") from exc
    names = (
        sorted(values["profiles"])
        if all_profiles
        else [profile_name or values.get("default_profile")]
    )
    if not names or any(name not in values["profiles"] for name in names):
        raise ValueError("Configure default_profile or select --profile / --all")
    profiles = [
        selected_profile({**config, "values": {**values, "default_profile": name}})
        for name in names
    ]
    paths = rule_paths(values)
    validate_report_paths(root, profiles, list(paths.values()))
    # Always read all three user files, even when the aggregation cache is reusable.
    for path in paths.values():
        if not path.is_file():
            raise ValueError(f"Missing report rules: {path}; run rules init first")
    app_rules = rules_from(paths["application_rules_path"])
    site_rules = rules_from(paths["site_rules_path"], sites=True)
    extraction = extraction_rules(paths["extraction_rules_path"])
    cutoff = report_cutoff(tz)
    resources = files("manictime_pipeline")
    data_code = b"".join(
        resources.joinpath(name).read_bytes()
        for name in ("report_data.py", "report_rules.py", "report_history.py")
    )
    view_code = b"".join(
        resources.joinpath(name).read_bytes()
        for name in ("reports.py", "resources/report.html", "resources/history.html")
    )
    rule_signature = hashlib.sha256(
        canonical([app_rules, site_rules, extraction, values["report_lookup_gap_minutes"]])
    ).hexdigest()
    lock_key = hashlib.sha256(str(root).casefold().encode()).hexdigest()
    report_lock = path_value(values["state_path"]) / "_report-locks" / lock_key
    with ExitStack() as stack:
        stack.enter_context(nullcontext() if dry_run else process_lock(report_lock))
        old = root_manifest(root)
        devices = dict(old["devices"])
        result = {
            "report_path": str(root),
            "index": str(root / "index.html"),
            "dry_run": dry_run,
            "devices": [],
        }
        cleanup = []
        for profile in profiles:
            with nullcontext() if dry_run else transaction.dataset_lock(profile):
                transaction.require_settled(profile)
                source = load_report_source(profile)
                if source is None:
                    raise ValueError(f"No successful ingest for {profile.name}; run ingest first")
                validate_artifacts(profile, source)
                activity_paths = sorted(
                    child_path(profile.processed_path, item["path"])
                    for item in source["artifacts"].values()
                    if item["path"].startswith("Ar_Activity/")
                )
                provenance = {
                    "generator_version": GENERATOR_VERSION,
                    "timezone": str(tz),
                    "device_id": profile.device_id,
                    "source_root": str(profile.processed_path),
                    "source_artifacts": {
                        key: value["sha256"] for key, value in source["artifacts"].items()
                    },
                    "application_rules": app_rules,
                    "site_rules": site_rules,
                    "extraction_rules": extraction,
                    "lookup_gap_minutes": values["report_lookup_gap_minutes"],
                    "data_code_sha256": hashlib.sha256(data_code).hexdigest(),
                }
                signature = hashlib.sha256(canonical(provenance)).hexdigest()
                previous = devices.get(profile.device_id)
                prior_record, data = None, None
                if previous:
                    prior_folder = child_path(root, previous["path"])
                    prior_record = validate_generation(
                        prior_folder, profile.device_id, previous["fingerprint"]
                    )
                    if prior_record and prior_record.get("data_signature") == signature:
                        cached = read_json(prior_folder / "data.json")
                        effective_cutoff = min(cutoff, cached["source_max_end"])
                        if prior_record["provenance"]["cutoff"] == effective_cutoff:
                            data = cached
                reused_data = data is not None
                history_payloads = {}
                if data is None:
                    data = build_data(
                        profile,
                        activity_paths,
                        tz,
                        cutoff,
                        app_rules,
                        site_rules,
                        values["report_lookup_gap_minutes"],
                        extraction,
                    )
                    history_payloads = data.pop("_history_payloads")
                    data["generator_version"] = GENERATOR_VERSION
                    data["rules_signature"] = rule_signature
                    data["source_captured_at"] = source.get(
                        "completed_at", source.get("started_at", "")
                    )
                # Groups also contain website favicons. Embed only icons actually used
                # by classified application rows in this report.
                used_icons = {
                    row.get("icon") for period in data["periods"].values() for row in period["apps"]
                }
                data["icons"] = {
                    key: value for key, value in data.get("icons", {}).items() if key in used_icons
                }
                # Old PCs stop advancing at the final source timestamp. A moving calendar alone
                # does not invalidate their data or HTML; incomplete current days still advance.
                effective_cutoff = min(cutoff, data["source_max_end"])
                # Stable presentation cutoff: include the last observed local date, even if
                # a retired PC stopped in the middle of that date.
                local_cutoff = datetime.fromtimestamp(effective_cutoff, tz)
                if local_cutoff.time() != time.min:
                    local_cutoff = datetime.combine(
                        local_cutoff.date() + timedelta(days=1), time.min, tzinfo=tz
                    )
                data["cutoff"] = local_cutoff.isoformat()
                provenance.update(
                    cutoff=effective_cutoff, view_sha256=hashlib.sha256(view_code).hexdigest()
                )
                fingerprint = hashlib.sha256(canonical(provenance)).hexdigest()
                relative = f"devices/{profile.device_id}/{fingerprint}"
                folder = child_path(root, relative)
                existing = validate_generation(folder, profile.device_id, fingerprint)
                action = (
                    "unchanged"
                    if existing
                    else "would_generate"
                    if dry_run
                    else "rendered"
                    if reused_data
                    else "generated"
                )
                if existing:
                    entry = existing["entry"]
                else:
                    entry = {
                        "device_id": profile.device_id,
                        "fingerprint": fingerprint,
                        "path": relative,
                        "first": data["first"],
                        "last": data["last"],
                        "last_day": data["last_day"],
                        "days": len(data["daily"]),
                        "periods": len(data["periods"]),
                        "dictionary_records": len(data["lookups"]),
                    }
                    if not dry_run:
                        if reused_data:
                            history_payloads = {
                                name: child_path(prior_folder, name).read_bytes()
                                for name in prior_record["outputs"]
                                if name.startswith("history/")
                            }
                        payloads = {**output_payloads(data), **history_payloads}
                        transaction.require_settled(profile)
                        if load_report_source(profile)["run_id"] != source["run_id"]:
                            raise ValueError(
                                "Inputs changed during report generation; rerun build-report"
                            )
                        validate_artifacts(profile, source)
                        for name, payload in payloads.items():
                            target = child_path(folder, name)
                            if target.exists() and target.read_bytes() != payload:
                                raise ValueError(
                                    f"Existing uncommitted report differs; preserve it: {target}"
                                )
                        for name, payload in payloads.items():
                            target = child_path(folder, name)
                            if not target.exists():
                                atomic_bytes(target, payload)
                        atomic_json(
                            folder / "manifest.json",
                            {
                                "schema_version": REPORT_FORMAT,
                                "device_id": profile.device_id,
                                "fingerprint": fingerprint,
                                "entry": entry,
                                "provenance": provenance,
                                "data_signature": signature,
                                "source_run_id": source["run_id"],
                                "outputs": {
                                    name: hashlib.sha256(payload).hexdigest()
                                    for name, payload in payloads.items()
                                },
                            },
                        )
                if previous and previous["fingerprint"] != fingerprint:
                    if previous.get("previous"):
                        cleanup.append(previous["previous"])
                    entry = {
                        **entry,
                        "previous": {k: v for k, v in previous.items() if k != "previous"},
                    }
                elif previous and previous.get("previous"):
                    entry = {**entry, "previous": previous["previous"]}
                devices[profile.device_id] = entry
                result["devices"].append(
                    {
                        "device_id": profile.device_id,
                        "action": action,
                        "aggregation_reused": reused_data,
                        "days": entry["days"],
                        "periods": entry["periods"],
                        "dictionary_records": entry["dictionary_records"],
                    }
                )
                if dry_run:
                    transaction.require_settled(profile)
                    if load_report_source(profile)["run_id"] != source["run_id"]:
                        raise ValueError(
                            "Inputs changed during preview; retry after ingest completes"
                        )
                    validate_artifacts(profile, source)
        if not dry_run:
            payload = combined_payload(root, devices, values["profiles"])
            history = history_page(payload)
            history_name = "history-" + hashlib.sha256(history).hexdigest() + ".html"
            history_target = child_path(root, history_name)
            if history_target.exists() and history_target.read_bytes() != history:
                raise ValueError(f"Edited history page is preserved: {history_target}")
            if not history_target.exists():
                atomic_bytes(history_target, history)
            payload["history_page"] = history_name
            index = render_report(payload)
            if (
                devices == old["devices"]
                and (root / "index.html").is_file()
                and sha256_file(root / "index.html") == hashlib.sha256(index).hexdigest()
                and not (root / ".index.pending.html").exists()
            ):
                return result
            record = {
                "schema_version": REPORT_FORMAT,
                "devices": devices,
                "history_page": history_name,
                "previous_history_page": old.get("history_page")
                if old.get("history_page") != history_name
                else old.get("previous_history_page"),
                "index_sha256": hashlib.sha256(index).hexdigest(),
                "previous_index_sha256": sha256_file(root / "index.html")
                if (root / "index.html").exists()
                else None,
            }
            atomic_bytes(root / ".index.pending.html", index)
            atomic_json(root / "report-manifest.json", record)
            os.replace(root / ".index.pending.html", root / "index.html")
            for entry in cleanup:
                clean_old_generation(root, entry)
            stale_history = old.get("previous_history_page")
            if stale_history and stale_history not in {
                history_name,
                record["previous_history_page"],
            }:
                match = re.fullmatch(r"history-([a-f0-9]{64})\.html", stale_history)
                if match:
                    stale_path = child_path(root, stale_history)
                    if stale_path.is_file() and sha256_file(stale_path) == match[1]:
                        stale_path.unlink()
        return result
