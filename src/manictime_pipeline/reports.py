"""Validated CSV inputs, immutable report generations and atomic HTML entry points."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import logging
import os
import uuid
from contextlib import ExitStack, nullcontext
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import transaction
from .config import path_value, selected_profile
from .io import atomic_json, child_path, process_lock, read_json, sha256_file
from .report_data import GENERATOR_VERSION, build_data, report_cutoff, rules_from
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
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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


def render_report(data: dict) -> bytes:
    template = files("manictime_pipeline").joinpath("resources/report.html").read_text("utf-8")
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
    if set(record["outputs"]) != REPORT_FILES:
        raise ValueError(f"Unexpected managed report files: {folder}")
    missing = False
    for relative, expected in record["outputs"].items():
        artifact = child_path(folder, relative)
        if not artifact.exists():
            missing = True
        elif sha256_file(artifact) != expected:
            raise ValueError(f"Edited generated report is preserved: {artifact}")
    return None if missing else record


def index_html(devices: dict) -> bytes:
    cards = []
    for device, entry in sorted(devices.items()):
        target = "/".join(quote(p) for p in Path(entry["path"]).parts) + "/index.html"
        cards.append(
            f'<a class="pc" href="{html.escape(target, quote=True)}">'
            f"<h2>{html.escape(device)}</h2><p>{html.escape(entry['first'][:10])} "
            f"〜 {html.escape(entry.get('last_day', entry['last'][:10]))}</p>"
            f"<span>{entry['days']:,} 日の記録 · 年／月／週を開く →</span></a>"
        )
    template = (
        files("manictime_pipeline").joinpath("resources/report-index.html").read_text("utf-8")
    )
    return template.replace("__CARDS__", "".join(cards)).encode("utf-8")


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
            raise ValueError("Keep editable rule CSVs outside report_path")


def clean_old_generation(root: Path, entry: dict) -> None:
    """Remove only verified, manifest-listed files in one exact generated directory."""
    folder = child_path(root, entry["path"])
    if folder.parent.parent != root / "devices" or folder.name != entry["fingerprint"]:
        raise ValueError("Unexpected old generation path")
    try:
        record = validate_generation(folder, entry["device_id"], entry["fingerprint"])
        if record is None:
            return
        if {p.name for p in folder.iterdir()} != REPORT_FILES | {"manifest.json"}:
            LOG.warning("Extra files in old report; preserving %s", folder)
            return
        for name in REPORT_FILES | {"manifest.json"}:
            child_path(folder, name).unlink()
        folder.rmdir()
    except (ValueError, OSError) as exc:
        LOG.warning("Old report preserved: %s", exc)


def build_report(config: dict, dry_run: bool = False, profile_name: str | None = None) -> dict:
    values = config["values"]
    root = path_value(values["report_path"])
    try:
        tz = ZoneInfo(values["report_timezone"])
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown report_timezone: {values['report_timezone']}") from exc
    names = [profile_name] if profile_name else sorted(values["profiles"])
    if not names:
        raise ValueError("Configure at least one profile before building reports")
    profiles = [
        selected_profile({**config, "values": {**values, "default_profile": name}})
        for name in names
    ]
    rule_paths = [
        path_value(values[k]) if k in values else None
        for k in ("application_rules_path", "site_rules_path")
    ]
    validate_report_paths(root, profiles, rule_paths)
    app_rules = rules_from(rule_paths[0])
    site_rules = rules_from(rule_paths[1], sites=True)
    cutoff = report_cutoff(tz)
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
                    "cutoff": cutoff,
                    "device_id": profile.device_id,
                    "source_root": str(profile.processed_path),
                    "source_artifacts": {
                        key: value["sha256"] for key, value in source["artifacts"].items()
                    },
                    "application_rules": app_rules,
                    "site_rules": site_rules,
                    "lookup_gap_minutes": values["report_lookup_gap_minutes"],
                    "template_sha256": hashlib.sha256(
                        files("manictime_pipeline").joinpath("resources/report.html").read_bytes()
                    ).hexdigest(),
                    "code_sha256": hashlib.sha256(
                        files("manictime_pipeline").joinpath("report_data.py").read_bytes()
                        + files("manictime_pipeline").joinpath("reports.py").read_bytes()
                    ).hexdigest(),
                }
                fingerprint = hashlib.sha256(canonical(provenance)).hexdigest()
                relative = f"devices/{profile.device_id}/{fingerprint}"
                folder = child_path(root, relative)
                existing = validate_generation(folder, profile.device_id, fingerprint)
                action = "unchanged" if existing else "would_generate" if dry_run else "generated"
                if existing:
                    entry = existing["entry"]
                else:
                    data = build_data(
                        profile,
                        activity_paths,
                        tz,
                        cutoff,
                        app_rules,
                        site_rules,
                        values["report_lookup_gap_minutes"],
                    )
                    data["generator_version"] = GENERATOR_VERSION
                    data["source_captured_at"] = source.get(
                        "completed_at", source.get("started_at", "")
                    )
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
                        payloads = output_payloads(data)
                        # Recheck read-only inputs before publishing a consistent generation.
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
                        record = {
                            "schema_version": REPORT_FORMAT,
                            "device_id": profile.device_id,
                            "fingerprint": fingerprint,
                            "entry": entry,
                            "provenance": provenance,
                            "source_run_id": source["run_id"],
                            "outputs": {
                                name: hashlib.sha256(payload).hexdigest()
                                for name, payload in payloads.items()
                            },
                        }
                        atomic_json(folder / "manifest.json", record)
                previous = devices.get(profile.device_id)
                if previous and previous["fingerprint"] != fingerprint:
                    # Retain the prior generation until a subsequent successful build.
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
            index = index_html(devices)
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
                "index_sha256": hashlib.sha256(index).hexdigest(),
                "previous_index_sha256": sha256_file(root / "index.html")
                if (root / "index.html").exists()
                else None,
            }
            # Each linked generation is complete before the entry page changes.
            # A pending index allows safe repair if interrupted between the two replacements.
            atomic_bytes(root / ".index.pending.html", index)
            atomic_json(root / "report-manifest.json", record)
            os.replace(root / ".index.pending.html", root / "index.html")
            for entry in cleanup:
                clean_old_generation(root, entry)
        return result
