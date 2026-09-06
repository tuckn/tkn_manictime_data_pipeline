"""Capture, compare, export, verify, and atomically publish a complete dataset."""

from __future__ import annotations

import csv
import logging
import sys
import uuid
from pathlib import Path

from . import __version__
from .config import Profile
from .database import DB_NAMES, backup, now, quick_check, readonly, schema
from .export import BOM, changes, export_changed, fingerprint
from .io import atomic_json, child_path, process_lock, read_json, sha256_file

LOG = logging.getLogger(__name__)
FORMAT_VERSION = "1.0.0"


def load_current(profile: Profile) -> dict | None:
    pointer = profile.processed_path / "current.json"
    if not pointer.exists():
        return None
    index = read_json(pointer)
    if index.get("schema_version") != FORMAT_VERSION:
        raise ValueError("Unsupported current.json schema_version")
    manifest_path = child_path(profile.processed_path, index["manifest"])
    if sha256_file(manifest_path) != index["sha256"]:
        raise ValueError(f"Manifest checksum mismatch: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != FORMAT_VERSION:
        raise ValueError("Unsupported manifest schema_version")
    if manifest.get("device_id") != profile.device_id:
        raise ValueError("Dataset device_id differs from the selected profile")
    if manifest.get("source_path") != str(profile.source_path.resolve()):
        raise ValueError(
            "Dataset source_path changed; use a separate profile/device for another DB"
        )
    csv_has_bom(manifest)
    expected_root = str(profile.raw_directory.resolve())
    legacy_root = str((profile.raw_directory / "Raw").resolve())
    allowed_roots = {expected_root}
    if manifest.get("tool_version") == "0.1.0" and csv_has_bom(manifest):
        allowed_roots.add(legacy_root)
    if manifest.get("raw_root") not in allowed_roots:
        raise ValueError(
            "Dataset raw_path changed; preserve its root or use a separate profile/device"
        )
    return manifest


def csv_has_bom(manifest: dict) -> bool:
    encoding = manifest.get("csv_contract", {}).get("encoding")
    if encoding not in {"UTF-8 with BOM", "UTF-8 without BOM"}:
        raise ValueError(f"Unsupported CSV encoding contract: {encoding!r}")
    return encoding == "UTF-8 with BOM"


def validate_artifacts(profile: Profile, manifest: dict, count_rows: bool = False) -> None:
    has_bom = csv_has_bom(manifest)
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    for key, artifact in manifest["artifacts"].items():
        path = child_path(profile.processed_path, artifact["path"])
        if not path.is_file() or sha256_file(path) != artifact["sha256"]:
            raise ValueError(f"Missing or edited output; restore it before ingest: {path}")
        with path.open("rb") as stream:
            if stream.read(len(BOM)).startswith(BOM) != has_bom:
                raise ValueError(f"CSV BOM does not match its manifest: {key}")
        if count_rows:
            with path.open(encoding="utf-8-sig" if has_bom else "utf-8", newline="") as stream:
                reader = csv.reader(stream)
                header = next(reader)
                expected = [c["name"] for c in manifest["tables"][artifact["table"]]["columns"]]
                if header != expected:
                    raise ValueError(f"CSV header mismatch: {key}")
                count = 0
                for row in reader:
                    if len(row) != len(header):
                        raise ValueError(f"CSV column count mismatch: {key}")
                    count += 1
            if count != artifact["rows"]:
                raise ValueError(f"CSV row count mismatch: {key}")


def _summary(plan: dict, fingerprints: dict) -> dict:
    return {
        "partitions": {action: len(keys) for action, keys in plan.items()},
        "rows": sum(v["rows"] for v in fingerprints.values()),
        "activity_rows": sum(
            v["rows"] for v in fingerprints.values() if v["table"] == "Ar_Activity"
        ),
        "export_rows": sum(fingerprints[k]["rows"] for k in plan["created"] + plan["updated"]),
        "changes": plan,
    }


def preview(profile: Profile) -> dict:
    directory = profile.source_directory()
    previous = load_current(profile)
    if previous:
        validate_artifacts(profile, previous)
    # A single read transaction prevents different tables seeing different commits.
    # It is never opened with immutable=1 against a live database.
    with readonly(directory / "ManicTimeReports.db") as connection:
        connection.execute("BEGIN")
        tables = schema(connection)
        fingerprints = fingerprint(connection, tables)
    with readonly(directory / "ManicTimeCore.db") as core:
        core.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
    plan = changes(fingerprints, previous["artifacts"] if previous else {})
    return {
        "action": "dry_run",
        "profile": profile.name,
        "source_directory": str(directory),
        "raw_root": str(profile.raw_directory),
        "processed_path": str(profile.processed_path),
        "raw_databases_to_capture": len(DB_NAMES),
        "source_db_bytes": sum((directory / n).stat().st_size for n in DB_NAMES),
        "note": "Read-only comparison; backup, quick_check and output writes are not performed.",
        **_summary(plan, fingerprints),
    }


def _record_run(profile: Profile, run_id: str, report: dict):
    try:
        atomic_json(profile.state_path / "runs" / f"{run_id}.json", report)
    except OSError:
        LOG.warning("Could not write operational run record for %s", run_id)


def ingest(profile: Profile, dry_run: bool = False) -> dict:
    profile.validate_paths()
    if dry_run:
        return preview(profile)
    directory = profile.source_directory()
    raw_root = profile.raw_directory
    # Both locks are released by the OS after a crash; leftover lock files are harmless.
    with process_lock(profile.processed_path), process_lock(raw_root):
        previous = load_current(profile)
        if previous:
            LOG.info("Checking previously published output")
            validate_artifacts(profile, previous)
        run_id = (
            now().replace("-", "").replace(":", "").replace("+0000", "Z")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        raw_stage = raw_root / f".{run_id}.partial"
        raw_final = raw_root / run_id
        runs = profile.processed_path / "runs"
        export_stage = runs / f".{run_id}.partial"
        export_final = runs / run_id
        report = {
            "schema_version": FORMAT_VERSION,
            "run_id": run_id,
            "started_at": now(),
            "profile": profile.name,
            "status": "running",
        }
        _record_run(profile, run_id, report)
        try:
            raw_stage.mkdir()
            captures = []
            for name in DB_NAMES:
                LOG.info("Capturing %s", name)
                captures.append(
                    backup(directory / name, raw_stage / name, profile.backup_timeout_seconds)
                )
            capture = {
                "schema_version": FORMAT_VERSION,
                "run_id": run_id,
                "tool_version": __version__,
                "device_id": profile.device_id,
                "databases": captures,
                "consistency": "Each DB is internally consistent; the two backups are sequential, "
                "not an application-wide transaction.",
            }
            atomic_json(raw_stage / "capture.json", capture)
            raw_stage.rename(raw_final)
            LOG.info("Raw capture saved: %s", raw_final)
            with readonly(raw_final / "ManicTimeReports.db") as connection:
                tables = schema(connection)
                fingerprints = fingerprint(connection, tables)
                plan = changes(fingerprints, previous["artifacts"] if previous else {})
                selected = set(plan["created"] + plan["updated"])
                export_stage.mkdir(parents=True)
                export_changed(connection, tables, fingerprints, selected, export_stage)
            artifacts = {}
            for key, details in fingerprints.items():
                if key in selected:
                    relative = (Path("runs") / run_id / f"{key}.csv").as_posix()
                    artifacts[key] = {**details, "path": relative, "origin_run_id": run_id}
                else:
                    artifacts[key] = previous["artifacts"][key]
            manifest = {
                "schema_version": FORMAT_VERSION,
                "tool_version": __version__,
                "run_id": run_id,
                "dataset_id": previous["dataset_id"] if previous else str(uuid.uuid4()),
                "device_id": profile.device_id,
                "profile": profile.name,
                "source_path": str(profile.source_path.resolve()),
                "raw_root": str(raw_root.resolve()),
                "capture": run_id,
                "capture_sha256": sha256_file(raw_final / "capture.json"),
                "started_at": report["started_at"],
                "completed_at": now(),
                "previous_run_id": previous["run_id"] if previous else None,
                "tables": tables,
                "artifacts": artifacts,
                "csv_contract": {
                    "encoding": "UTF-8 without BOM",
                    "line_ending": "LF",
                    "null": r"\N",
                    "blob": r"\B followed by base64",
                    "escape": "Text beginning with backslash gets one extra leading backslash",
                    "time": "Source timestamps preserved; *UtcTime is UTC, *LocalTime is "
                    "source wall time. Partition by StartLocalTime year/month.",
                },
                "summary": _summary(plan, fingerprints),
            }
            atomic_json(export_stage / "manifest.json", manifest)
            export_stage.rename(export_final)
            manifest_path = export_final / "manifest.json"
            pointer = {
                "schema_version": FORMAT_VERSION,
                "manifest": manifest_path.relative_to(profile.processed_path).as_posix(),
                "sha256": sha256_file(manifest_path),
            }
            # The only mutable dataset pointer changes after every artifact is complete.
            atomic_json(profile.processed_path / "current.json", pointer)
        except BaseException as exc:
            report.update(
                status="failed",
                completed_at=now(),
                error_type=type(exc).__name__,
                error=str(exc),
                raw_path=str(raw_final if raw_final.exists() else raw_stage),
                export_staging_path=str(export_stage),
            )
            _record_run(profile, run_id, report)
            raise
        result = {
            "action": "created"
            if previous is None
            else "updated"
            if selected or plan["removed"]
            else "unchanged",
            "run_id": run_id,
            "profile": profile.name,
            "raw_capture": str(raw_final),
            "current": str(profile.processed_path / "current.json"),
            "manifest": str(manifest_path),
            **manifest["summary"],
        }
        report.update(status="completed", completed_at=now(), result=result)
        _record_run(profile, run_id, report)
        return result


def verify(profile: Profile) -> dict:
    manifest = load_current(profile)
    if manifest is None:
        raise ValueError("No published dataset; run ingest first")
    LOG.info("Verifying CSV checksums, headers and row counts")
    validate_artifacts(profile, manifest, count_rows=True)
    capture_path = child_path(Path(manifest["raw_root"]), manifest["capture"])
    if sha256_file(capture_path / "capture.json") != manifest["capture_sha256"]:
        raise ValueError("Raw capture manifest checksum mismatch")
    capture = read_json(capture_path / "capture.json")
    if capture.get("schema_version") != FORMAT_VERSION:
        raise ValueError("Unsupported capture schema")
    if {item["file"] for item in capture["databases"]} != set(DB_NAMES):
        raise ValueError("Raw capture is missing required databases")
    for item in capture["databases"]:
        path = child_path(capture_path, item["file"])
        if path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise ValueError(f"Raw database checksum mismatch: {path}")
        with readonly(path) as connection:
            quick_check(connection)
    LOG.info("Comparing the published dataset with its Raw Reports snapshot")
    with readonly(capture_path / "ManicTimeReports.db") as connection:
        tables = schema(connection)
        if tables != manifest["tables"]:
            raise ValueError("Raw schema does not match published schema")
        expected = fingerprint(connection, tables, utf8_bom=csv_has_bom(manifest))
    diff = changes(expected, manifest["artifacts"])
    if any(diff[k] for k in ("created", "updated", "removed")):
        raise ValueError("Published CSV dataset differs from its Raw snapshot")
    return {
        "action": "verified",
        "run_id": manifest["run_id"],
        "partitions": len(expected),
        "rows": sum(a["rows"] for a in expected.values()),
        "activity_rows": sum(a["rows"] for a in expected.values() if a["table"] == "Ar_Activity"),
        "current": str(profile.processed_path / "current.json"),
    }
