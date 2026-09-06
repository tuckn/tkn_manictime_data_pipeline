"""Publish the latest Raw and CSV together at stable, directly usable paths."""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from pathlib import Path

from . import __version__, legacy, raw, transaction
from .config import Profile
from .database import DB_NAMES, backup, now, readonly, readonly_snapshot, schema
from .export import changes, export_changed, fingerprint
from .io import atomic_json, child_path, read_json, sha256_file
from .legacy import csv_has_bom, validate_artifacts

LOG = logging.getLogger(__name__)
FORMAT_VERSION = "3.0.0"
READ_VERSIONS = {"2.0.0", FORMAT_VERSION}


def _run_id() -> str:
    return (
        now().replace("-", "").replace(":", "").replace("+0000", "Z") + "-" + uuid.uuid4().hex[:8]
    )


def load_current(profile: Profile) -> dict | None:
    pointer = profile.state_path / "current.json"
    if not pointer.exists():
        return None
    index = read_json(pointer)
    if index.get("schema_version") not in READ_VERSIONS:
        raise ValueError("Unsupported current.json schema_version")
    manifest_path = child_path(profile.state_path, index["manifest"])
    if sha256_file(manifest_path) != index["sha256"]:
        raise ValueError(f"Manifest checksum mismatch: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != index["schema_version"]:
        raise ValueError("Unsupported run record schema_version")
    for key, expected in {
        "device_id": profile.device_id,
        "profile": profile.name,
        "source_path": str(profile.source_path.resolve()),
        "raw_config_root": str(profile.raw_directory.resolve()),
        "processed_root": str(profile.processed_path.resolve()),
    }.items():
        if manifest.get(key) != expected:
            raise ValueError(f"Dataset {key} changed; restore settings or use a separate dataset")
    if index["manifest"] != f"runs/{manifest['run_id']}.json":
        raise ValueError("Current run record path mismatch")
    csv_has_bom(manifest)
    raw_root = Path(manifest["raw_root"]).resolve()
    if raw_root not in {profile.raw_directory.resolve(), (profile.raw_directory / "Raw").resolve()}:
        raise ValueError("Raw capture root differs from configured storage")
    if manifest["schema_version"] == FORMAT_VERSION:
        if manifest.get("raw_layout") != "latest" or raw_root != profile.raw_directory.resolve():
            raise ValueError("Expected latest Raw in the configured device directory")
        raw.database_items(manifest["raw_capture"])
        if not isinstance(manifest.get("activity_stats"), dict):
            raise ValueError("Missing activity statistics in current state")
    for key, artifact in manifest["artifacts"].items():
        if artifact["path"] != f"{key}.csv":
            raise ValueError(f"Expected a fixed CSV path: {key}")
        child_path(profile.processed_path, artifact["path"])
    return manifest


def unmanaged_csv(profile: Profile, previous: dict | None) -> list[str]:
    owned = {a["path"] for a in previous["artifacts"].values()} if previous else set()
    found = []
    for path in profile.processed_path.rglob("*.csv"):
        relative = path.relative_to(profile.processed_path)
        if relative.parts[0] == "pipeline-v1":
            continue  # Legacy output is preserved until a separate, deliberate cleanup.
        if relative.as_posix() not in owned:
            found.append(relative.as_posix())
    return sorted(found)


def _check_raw_destination(profile: Profile, previous: dict | None) -> None:
    if not previous or previous.get("raw_layout") != "latest":
        for name in DB_NAMES:
            if (profile.raw_directory / name).exists():
                raise ValueError(
                    f"Unmanaged Raw file; preserve it separately before publication: "
                    f"{profile.raw_directory / name}"
                )


def _preflight(profile: Profile, *, allow_legacy: bool = False) -> dict | None:
    transaction.require_settled(profile)
    previous = load_current(profile)
    if previous is None and (profile.processed_path / "pipeline-v1/current.json").exists():
        raise ValueError("Legacy layout found; run migrate-layout --dry-run, then migrate-layout")
    unknown = unmanaged_csv(profile, previous)
    if unknown:
        raise ValueError(
            f"Unmanaged CSV files exist; preserve them in a separate folder first: "
            f"{unknown[:5]} ({len(unknown)} files)"
        )
    if previous:
        validate_artifacts(profile, previous)
        raw.validate(previous)
        if previous.get("raw_layout") != "latest" and not allow_legacy:
            raise ValueError(
                "Versioned Raw layout found; run migrate-layout --dry-run, "
                "then migrate-layout before ingest"
            )
    if not allow_legacy:
        _check_raw_destination(profile, previous)
    return previous


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
    previous = _preflight(profile)
    directory = profile.source_directory()
    with readonly(directory / "ManicTimeReports.db") as connection:
        connection.execute("BEGIN")
        fingerprints = fingerprint(connection, schema(connection))
        stats = raw.statistics(connection)
    with readonly(directory / "ManicTimeCore.db") as core:
        core.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
    plan = changes(fingerprints, previous["artifacts"] if previous else {})
    check = raw.check_drop(
        raw.previous_statistics(previous), stats, profile.max_activity_drop_percent
    )
    if not check["passed"]:
        raise raw.ActivityDropError(check)
    return {
        "action": "dry_run",
        "profile": profile.name,
        "source_directory": str(directory),
        "raw_root": str(profile.raw_directory),
        "processed_path": str(profile.processed_path),
        "state_path": str(profile.state_path),
        "raw_databases_to_capture": len(DB_NAMES),
        "raw_layout": "latest",
        "activity_drop_check": check,
        "source_db_bytes": sum((directory / n).stat().st_size for n in DB_NAMES),
        "note": "Read-only comparison; no backup, quick_check or output writes.",
        **_summary(plan, fingerprints),
    }


def _manifest(profile, run_id, previous, started, capture, tables, fingerprints, plan, stats):
    old = previous["artifacts"] if previous else {}
    return {
        "schema_version": FORMAT_VERSION,
        "tool_version": __version__,
        "status": "completed",
        "run_id": run_id,
        "dataset_id": previous["dataset_id"] if previous else str(uuid.uuid4()),
        "profile": profile.name,
        "device_id": profile.device_id,
        "source_path": str(profile.source_path.resolve()),
        "raw_config_root": str(profile.raw_directory.resolve()),
        "processed_root": str(profile.processed_path.resolve()),
        "raw_root": str(profile.raw_directory.resolve()),
        "raw_layout": "latest",
        "activity_stats": stats,
        "raw_capture": capture,
        "effective_config": {
            "source_path": str(profile.source_path.resolve()),
            "device_id": profile.device_id,
            "raw_path": str(profile.raw_path.resolve()),
            "processed_data_path": str(profile.processed_path.parent.resolve()),
            "state_path": str(profile.state_path.parent.resolve()),
            "backup_timeout_seconds": profile.backup_timeout_seconds,
            "max_activity_drop_percent": profile.max_activity_drop_percent,
        },
        "started_at": started,
        "completed_at": now(),
        "previous_run_id": previous["run_id"] if previous else None,
        "tables": tables,
        "artifacts": {
            key: {
                **detail,
                "path": f"{key}.csv",
                "origin_run_id": old[key].get("origin_run_id", run_id)
                if key in plan["unchanged"]
                else run_id,
            }
            for key, detail in fingerprints.items()
        },
        "csv_contract": {
            "encoding": "UTF-8 without BOM",
            "line_ending": "LF",
            "null": r"\N",
            "blob": r"\B followed by base64",
            "escape": "Text beginning with backslash gets one extra leading backslash",
            "time": "Source timestamps preserved; *UtcTime is UTC, *LocalTime is source wall time. "
            "Partition by StartLocalTime year/month.",
        },
        "summary": _summary(plan, fingerprints),
    }


def _publish(profile, folder, previous, manifest):
    journal = transaction.prepare(profile, folder, previous, manifest)
    transaction.apply(profile, folder, journal)
    # Verify all final files (including unchanged partitions) before the commit marker.
    validate_artifacts(profile, manifest)
    raw.validate(manifest)
    manifest_path = profile.state_path / "runs" / f"{manifest['run_id']}.json"
    atomic_json(manifest_path, manifest)
    atomic_json(
        profile.state_path / "current.json",
        {
            "schema_version": FORMAT_VERSION,
            "manifest": manifest_path.relative_to(profile.state_path).as_posix(),
            "sha256": sha256_file(manifest_path),
        },
    )
    # A crash after the pointer update is a committed run; recover only cleans staging.
    try:
        transaction.clear_transaction(profile, folder)
    except OSError:
        LOG.warning("Data committed; run recover to clear leftover staging")
    return manifest_path


def _failed(profile, run_id, report, exc):
    if transaction.is_committed(profile, run_id):
        return  # Never rewrite an immutable record referenced by current.json.
    if isinstance(exc, raw.ActivityDropError):
        report["activity_drop_check"] = exc.check
    report.update(
        status="failed", completed_at=now(), error_type=type(exc).__name__, error=str(exc)
    )
    try:
        atomic_json(profile.state_path / "runs" / f"{run_id}.json", report)
        transaction.recover_pending(profile)
    except (OSError, ValueError) as recovery_error:
        LOG.error("Recovery incomplete: %s. Run recover before using CSV data.", recovery_error)


def ingest(profile: Profile, dry_run: bool = False) -> dict:
    profile.validate_paths()
    if dry_run:
        return preview(profile)
    directory = profile.source_directory()
    with transaction.dataset_lock(profile):
        transaction.recover_pending(profile)
        previous = _preflight(profile)
        run_id = _run_id()
        folder = profile.state_path / "transactions" / run_id
        raw_final = profile.raw_directory
        report = {
            "schema_version": FORMAT_VERSION,
            "tool_version": __version__,
            "run_id": run_id,
            "profile": profile.name,
            "started_at": now(),
            "status": "running",
            "raw_path": str(raw_final),
            "raw_staging_path": str(profile.raw_directory / f".{run_id}.partial"),
        }
        # A state write failure is fatal, before any Raw/CSV mutation.
        atomic_json(profile.state_path / "runs" / f"{run_id}.json", report)
        try:
            folder.mkdir(parents=True)
            raw_stage = transaction.begin_raw(profile, folder)
            capture = {
                "schema_version": FORMAT_VERSION,
                "run_id": run_id,
                "tool_version": __version__,
                "device_id": profile.device_id,
                "databases": [],
                "consistency": "Each DB is internally consistent; "
                "the two backups are sequential, not an application-wide transaction.",
            }
            report["raw_capture"] = capture
            for name in DB_NAMES:
                LOG.info("Capturing %s", name)
                capture["databases"].append(
                    backup(directory / name, raw_stage / name, profile.backup_timeout_seconds)
                )
                atomic_json(profile.state_path / "runs" / f"{run_id}.json", report)
            LOG.info("Raw candidate captured; validating before replacing the previous snapshot")
            with readonly_snapshot(raw_stage / "ManicTimeReports.db") as connection:
                tables = schema(connection)
                fingerprints = fingerprint(connection, tables)
                stats = raw.statistics(connection)
                check = raw.check_drop(
                    raw.previous_statistics(previous), stats, profile.max_activity_drop_percent
                )
                report["activity_drop_check"] = check
                if not check["passed"]:
                    raise raw.ActivityDropError(check)
                plan = changes(fingerprints, previous["artifacts"] if previous else {})
                selected = set(plan["created"] + plan["updated"])
                export_changed(connection, tables, fingerprints, selected, folder / "new")
            manifest = _manifest(
                profile,
                run_id,
                previous,
                report["started_at"],
                capture,
                tables,
                fingerprints,
                plan,
                stats,
            )
            manifest["activity_drop_check"] = check
            manifest_path = _publish(profile, folder, previous, manifest)
        except BaseException as exc:
            _failed(profile, run_id, report, exc)
            raise
        return {
            "action": "created"
            if previous is None
            else "updated"
            if selected or plan["removed"]
            else "unchanged",
            "run_id": run_id,
            "profile": profile.name,
            "raw_capture": str(raw_final),
            "processed_path": str(profile.processed_path),
            "current": str(profile.state_path / "current.json"),
            "manifest": str(manifest_path),
            **manifest["summary"],
        }


def recover(profile: Profile, dry_run: bool = False) -> dict:
    profile.validate_paths()
    if dry_run:
        return {"action": "dry_run", "pending_runs": [p.name for p in transaction.pending(profile)]}
    with transaction.dataset_lock(profile):
        results = transaction.recover_pending(profile)
    return {"action": "recovered" if results else "unchanged", "runs": results}


def verify(profile: Profile) -> dict:
    profile.validate_paths()
    manifest = _preflight(profile, allow_legacy=True)
    if manifest is None:
        raise ValueError("No published dataset; run ingest first")
    LOG.info("Verifying current CSV and Raw capture")
    validate_artifacts(profile, manifest, count_rows=True)
    raw.validate(manifest, integrity=True)
    capture_path = raw.directory(manifest)
    with readonly_snapshot(capture_path / "ManicTimeReports.db") as connection:
        tables = schema(connection)
        if tables != manifest["tables"]:
            raise ValueError("Raw schema does not match published schema")
        expected = fingerprint(connection, tables, utf8_bom=csv_has_bom(manifest))
        if (
            manifest.get("raw_layout") == "latest"
            and raw.statistics(connection) != manifest["activity_stats"]
        ):
            raise ValueError("Raw activity statistics differ from recorded state")
    diff = changes(expected, manifest["artifacts"])
    if any(diff[k] for k in ("created", "updated", "removed")):
        raise ValueError("Published CSV dataset differs from its Raw snapshot")
    return {
        "action": "verified",
        "run_id": manifest["run_id"],
        "partitions": len(expected),
        "rows": sum(a["rows"] for a in expected.values()),
        "activity_rows": sum(a["rows"] for a in expected.values() if a["table"] == "Ar_Activity"),
        "current": str(profile.state_path / "current.json"),
    }


def migrate_layout(profile: Profile, dry_run: bool = False) -> dict:
    """Copy verified v0.1-v0.3 data into latest Raw/fixed CSV, preserving legacy files."""
    profile.validate_paths()
    if dry_run:
        return _migrate(profile, True)
    with transaction.dataset_lock(profile):
        transaction.recover_pending(profile)
        return _migrate(profile, False)


def _migrate(profile: Profile, dry_run: bool) -> dict:
    transaction.require_settled(profile)
    previous = load_current(profile)
    if previous is not None and previous.get("raw_layout") == "latest":
        raise ValueError("Latest Raw layout already exists; use ingest")
    csv_previous = previous
    old_profile = replace(profile, processed_path=profile.processed_path / "pipeline-v1")
    if previous is None:
        previous = legacy.load_current(old_profile)
        if previous is None:
            raise ValueError("No legacy dataset to migrate")
    conflicts = unmanaged_csv(profile, csv_previous)
    _check_raw_destination(profile, previous)
    if dry_run:
        validate_artifacts(profile if csv_previous else old_profile, previous)
        return {
            "action": "dry_run",
            "can_migrate": not conflicts,
            "conflicting_csvs": conflicts,
            "partitions": len(previous["artifacts"]),
            "from_schema_version": previous["schema_version"],
            "raw_layout": "latest",
            "processed_path": str(profile.processed_path),
            "raw_directory": str(profile.raw_directory),
            "state_path": str(profile.state_path),
            "note": "Legacy files remain untouched. Raw is copied to fixed paths; "
            "no new live capture. Full Raw validation runs before actual migration.",
        }
    if conflicts:
        raise ValueError(
            f"Unmanaged CSV files block migration; preserve them separately first: "
            f"{conflicts[:5]} ({len(conflicts)} files)"
        )
    if csv_previous:
        verify(profile)
        capture = previous["raw_capture"]
    else:
        legacy.verify(old_profile)
        capture = read_json(raw.directory(previous) / "capture.json")
    run_id = _run_id()
    folder = profile.state_path / "transactions" / run_id
    report = {
        "schema_version": FORMAT_VERSION,
        "run_id": run_id,
        "status": "running",
        "started_at": now(),
        "operation": "migrate-layout",
    }
    atomic_json(profile.state_path / "runs" / f"{run_id}.json", report)
    try:
        folder.mkdir(parents=True)
        stage = transaction.begin_raw(profile, folder)
        for name, detail in raw.database_items(capture).items():
            transaction.copy_checked(raw.directory(previous) / name, stage / name, detail["sha256"])
        with readonly_snapshot(stage / "ManicTimeReports.db") as connection:
            tables = schema(connection)
            fingerprints = fingerprint(connection, tables)
            stats = raw.statistics(connection)
            plan = changes(fingerprints, csv_previous["artifacts"] if csv_previous else {})
            export_changed(
                connection,
                tables,
                fingerprints,
                set(plan["created"] + plan["updated"]),
                folder / "new",
            )
        manifest = _manifest(
            profile,
            run_id,
            previous,
            report["started_at"],
            capture,
            tables,
            fingerprints,
            plan,
            stats,
        )
        manifest["migration"] = {
            "from_format": previous["schema_version"],
            "legacy_manifest": previous,
            "legacy_raw_directory": str(raw.directory(previous)),
            "legacy_files_preserved": True,
        }
        manifest_path = _publish(profile, folder, csv_previous, manifest)
    except BaseException as exc:
        _failed(profile, run_id, report, exc)
        raise
    return {
        "action": "migrated",
        "run_id": run_id,
        "manifest": str(manifest_path),
        "current": str(profile.state_path / "current.json"),
        "raw_capture": str(profile.raw_directory),
        "processed_path": str(profile.processed_path),
        "legacy_raw_preserved": str(raw.directory(previous)),
        **manifest["summary"],
    }
