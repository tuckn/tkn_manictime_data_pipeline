"""Recoverable publication of latest Raw and CSV; durable bookkeeping lives in state."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .config import Profile
from .database import DB_NAMES, check_snapshot_sidecars
from .io import atomic_json, child_path, process_lock, read_json, sha256_file
from .raw import database_items


def file_hash(path: Path) -> str | None:
    return sha256_file(path) if path.exists() else None


@contextmanager
def dataset_lock(profile: Profile):
    # Use the same state parent for every profile writing to shared storage.
    roots = sorted(
        {str(p.resolve()).casefold() for p in (profile.raw_directory, profile.processed_path)}
    )
    with ExitStack() as stack:
        stack.enter_context(process_lock(profile.state_path))
        for root in roots:
            key = hashlib.sha256(root.encode("utf-8")).hexdigest()
            stack.enter_context(process_lock(profile.state_path.parent / "_locks" / key))
        yield


def copy_checked(source: Path, destination: Path, expected: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    if sha256_file(destination) != expected:
        raise ValueError(f"Copy checksum mismatch: {destination}")


def _temporary(path: Path, run_id: str) -> Path:
    return path.with_name(f".{path.name}.{run_id}.tmp")


def install(source: Path, destination: Path, expected: str, run_id: str) -> None:
    """The final rename stays on the destination filesystem, even with separate state."""
    temporary = _temporary(destination, run_id)
    try:
        copy_checked(source, temporary, expected)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def begin_raw(profile: Profile, folder: Path) -> Path:
    """Record the owned staging location before creating any candidate Raw files."""
    descriptor = {
        "schema_version": "1.0.0",
        "raw_root": str(profile.raw_directory.resolve()),
        "relative": f".{folder.name}.partial",
        "run_id": folder.name,
    }
    atomic_json(folder / "raw-staging.json", descriptor)
    stage = raw_stage(profile, folder)
    stage.mkdir(parents=True)
    (stage / "new").mkdir()
    (stage / "old").mkdir()
    return stage / "new"


def raw_stage(profile: Profile, folder: Path) -> Path:
    descriptor = read_json(folder / "raw-staging.json")
    if (
        descriptor.get("schema_version") != "1.0.0"
        or descriptor.get("raw_root") != str(profile.raw_directory.resolve())
        or descriptor.get("run_id") != folder.name
        or descriptor.get("relative") != f".{folder.name}.partial"
    ):
        raise ValueError("Raw staging binding mismatch; restore state before recovery")
    result = child_path(profile.raw_directory, descriptor["relative"])
    if result.parent != profile.raw_directory.resolve():
        raise ValueError("Raw staging directory is outside its expected parent")
    return result


def clear_transaction(profile: Profile, folder: Path) -> None:
    root = (profile.state_path / "transactions").resolve()
    target = folder.resolve()
    if target.parent != root or folder.is_symlink():
        raise ValueError(f"Unsafe transaction cleanup path: {folder}")
    if (folder / "raw-staging.json").exists():
        stage = raw_stage(profile, folder)
        if stage.exists():
            # Only the run-owned DB staging tree may be removed, never the Raw root.
            allowed = {
                Path(part) / (name + suffix)
                for part in ("old", "new")
                for name in DB_NAMES
                for suffix in ("", "-journal", "-wal", "-shm")
            }
            for item in stage.rglob("*"):
                relative = item.relative_to(stage)
                if (
                    item.is_symlink()
                    or not item.resolve().is_relative_to(stage)
                    or (item.is_dir() and relative not in {Path("old"), Path("new")})
                    or (item.is_file() and relative not in allowed)
                ):
                    raise ValueError(
                        f"Unexpected Raw staging content; preserve and inspect: {item}"
                    )
            shutil.rmtree(stage)
    # Raw staging must be gone before its descriptor can be removed.
    shutil.rmtree(target)


def pending(profile: Profile) -> list[Path]:
    return sorted(p for p in (profile.state_path / "transactions").glob("*") if p.is_dir())


def require_settled(profile: Profile) -> None:
    if pending(profile):
        raise ValueError(
            "Unfinished Raw/CSV update; run recover before reading or verifying CSV data"
        )


def prepare(profile: Profile, folder: Path, previous: dict | None, manifest: dict) -> dict:
    old = previous["artifacts"] if previous else {}
    new = manifest["artifacts"]
    operations = []
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key), new.get(key)
        old_sha = before["sha256"] if before else None
        new_sha = after["sha256"] if after else None
        if old_sha == new_sha:
            continue
        relative = f"{key}.csv"
        target = child_path(profile.processed_path, relative)
        if file_hash(target) != old_sha:
            raise ValueError(f"Existing or edited CSV cannot be overwritten: {target}")
        if before:
            copy_checked(target, child_path(folder / "old", relative), old_sha)
        if after and file_hash(child_path(folder / "new", relative)) != new_sha:
            raise ValueError(f"Prepared CSV checksum mismatch: {relative}")
        operations.append({"path": relative, "old_sha256": old_sha, "new_sha256": new_sha})
    if manifest.get("raw_layout") == "latest":
        stage = raw_stage(profile, folder)
        old_raw = (
            database_items(previous["raw_capture"])
            if previous and previous.get("raw_layout") == "latest"
            else {}
        )
        raw_operations = []
        for name, item in database_items(manifest["raw_capture"]).items():
            old_sha = old_raw[name]["sha256"] if name in old_raw else None
            new_sha = item["sha256"]
            target = child_path(profile.raw_directory, name)
            if file_hash(target) != old_sha:
                raise ValueError(f"Existing or edited Raw cannot be overwritten: {target}")
            if file_hash(stage / "new" / name) != new_sha:
                raise ValueError(f"Prepared Raw checksum mismatch: {name}")
            if old_sha != new_sha:
                raw_operations.append(
                    {"storage": "raw", "path": name, "old_sha256": old_sha, "new_sha256": new_sha}
                )
        operations = raw_operations + operations
    journal = {
        "schema_version": "2.0.0",
        "raw_root": str(profile.raw_directory.resolve()),
        "run_id": folder.name,
        "processed_root": str(profile.processed_path.resolve()),
        "previous_current_sha256": file_hash(profile.state_path / "current.json"),
        "operations": operations,
    }
    # CSV backups and the journal are durable before publication. Raw is moved, not copied.
    atomic_json(folder / "journal.json", journal)
    return journal


def operation_paths(profile: Profile, folder: Path, op: dict) -> tuple[Path, Path, Path]:
    storage = op.get("storage", "csv")  # v0.3 journals contained only CSV operations.
    if storage == "raw":
        if op["path"] not in DB_NAMES:
            raise ValueError("Unexpected Raw file in transaction journal")
        stage = raw_stage(profile, folder)
        return (
            child_path(profile.raw_directory, op["path"]),
            stage / "old" / op["path"],
            stage / "new" / op["path"],
        )
    if storage != "csv":
        raise ValueError("Unsupported transaction storage")
    return (
        child_path(profile.processed_path, op["path"]),
        child_path(folder / "old", op["path"]),
        child_path(folder / "new", op["path"]),
    )


def apply(profile: Profile, folder: Path, journal: dict) -> None:
    for op in journal["operations"]:
        target, saved, staged = operation_paths(profile, folder, op)
        if file_hash(target) != op["old_sha256"]:
            raise ValueError(f"Data changed during publication: {target}")
        if op.get("storage") == "raw":
            check_snapshot_sidecars(target)
            # Same-filesystem renames retain old + new bytes only, without a third DB copy.
            if op["old_sha256"] is not None:
                target.rename(saved)
            staged.rename(target)
        elif op["new_sha256"] is None:
            target.unlink()
        else:
            install(staged, target, op["new_sha256"], folder.name)


def validate_rollback(profile: Profile, folder: Path, journal: dict) -> None:
    for op in journal["operations"]:
        target, saved, _ = operation_paths(profile, folder, op)
        actual = file_hash(target)
        allowed = {op["old_sha256"], op["new_sha256"]}
        if op.get("storage") == "raw":
            check_snapshot_sidecars(target)
            allowed.add(None)  # Crash between moving the old DB aside and installing the new DB.
        if actual not in allowed:
            raise ValueError(f"Data edited during recovery; restore it first: {target}")
        old = op["old_sha256"]
        if op.get("storage") == "raw":
            if saved.exists() and file_hash(saved) != old:
                raise ValueError(f"Rollback backup checksum mismatch: {saved}")
            if old is not None and actual != old and file_hash(saved) != old:
                raise ValueError(f"Missing Raw rollback backup: {saved}")
        elif old is not None and file_hash(saved) != old:
            raise ValueError(f"Rollback backup checksum mismatch: {saved}")


def rollback(profile: Profile, folder: Path, journal: dict) -> None:
    validate_rollback(profile, folder, journal)
    for op in reversed(journal["operations"]):
        target, saved, _ = operation_paths(profile, folder, op)
        if op.get("storage") != "raw":
            _temporary(target, folder.name).unlink(missing_ok=True)
        if file_hash(target) != op["old_sha256"]:
            if op["old_sha256"] is None:
                target.unlink(missing_ok=True)
            elif op.get("storage") == "raw":
                os.replace(saved, target)
            else:
                install(saved, target, op["old_sha256"], folder.name)


def is_committed(profile: Profile, run_id: str) -> bool:
    path = profile.state_path / "current.json"
    if not path.exists():
        return False
    pointer = read_json(path)
    if pointer.get("manifest") != f"runs/{run_id}.json":
        return False
    record = child_path(profile.state_path, pointer["manifest"])
    if sha256_file(record) != pointer["sha256"]:
        raise ValueError("Current run record checksum mismatch; restore state before recovery")
    return True


def recover_pending(profile: Profile) -> list[dict]:
    results = []
    for folder in pending(profile):
        record = profile.state_path / "runs" / f"{folder.name}.json"
        if (
            not re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8}", folder.name)
            or not record.is_file()
        ):
            raise ValueError(f"Unknown transaction or missing run record; restore state: {folder}")
        report = read_json(record)
        if report.get("run_id") != folder.name:
            raise ValueError(f"Transaction run record mismatch: {record}")
        committed = is_committed(profile, folder.name)
        journal_path = folder / "journal.json"
        if journal_path.exists():
            journal = read_json(journal_path)
            if (
                journal.get("schema_version") not in {"1.0.0", "2.0.0"}
                or (
                    journal.get("schema_version") == "2.0.0"
                    and journal.get("raw_root") != str(profile.raw_directory.resolve())
                )
                or journal.get("run_id") != folder.name
                or journal.get("processed_root") != str(profile.processed_path.resolve())
            ):
                raise ValueError(f"Transaction binding mismatch: {folder}")
            if not committed:
                if (
                    file_hash(profile.state_path / "current.json")
                    != journal["previous_current_sha256"]
                ):
                    raise ValueError(
                        "State changed during an unfinished transaction; restore state"
                    )
                rollback(profile, folder, journal)
        if not committed:
            report.update(status="failed", recovery="Rolled back or abandoned before publication")
            atomic_json(record, report)
        clear_transaction(profile, folder)
        results.append(
            {"run_id": folder.name, "action": "committed" if committed else "rolled_back"}
        )
    return results
