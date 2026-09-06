"""Recoverable replacement of fixed CSV paths; all durable bookkeeping is in state."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .config import Profile
from .io import atomic_json, child_path, process_lock, read_json, sha256_file


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


def clear_transaction(profile: Profile, folder: Path) -> None:
    root = (profile.state_path / "transactions").resolve()
    target = folder.resolve()
    if target.parent != root or folder.is_symlink():
        raise ValueError(f"Unsafe transaction cleanup path: {folder}")
    shutil.rmtree(target)


def pending(profile: Profile) -> list[Path]:
    return sorted(p for p in (profile.state_path / "transactions").glob("*") if p.is_dir())


def require_settled(profile: Profile) -> None:
    if pending(profile):
        raise ValueError("Unfinished CSV update; run recover before reading or verifying CSV data")


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
    journal = {
        "schema_version": "1.0.0",
        "run_id": folder.name,
        "processed_root": str(profile.processed_path.resolve()),
        "previous_current_sha256": file_hash(profile.state_path / "current.json"),
        "operations": operations,
    }
    # No published CSV is changed until every old file and this journal are durable.
    atomic_json(folder / "journal.json", journal)
    return journal


def apply(profile: Profile, folder: Path, journal: dict) -> None:
    for op in journal["operations"]:
        target = child_path(profile.processed_path, op["path"])
        if file_hash(target) != op["old_sha256"]:
            raise ValueError(f"CSV changed during publication: {target}")
        if op["new_sha256"] is None:
            target.unlink()
        else:
            install(child_path(folder / "new", op["path"]), target, op["new_sha256"], folder.name)


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
                journal.get("schema_version") != "1.0.0"
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
                # Check the entire rollback first; never overwrite an unrelated manual edit.
                for op in journal["operations"]:
                    target = child_path(profile.processed_path, op["path"])
                    if file_hash(target) not in {op["old_sha256"], op["new_sha256"]}:
                        raise ValueError(f"CSV edited during recovery; restore it first: {target}")
                    if op["old_sha256"] is not None:
                        saved = child_path(folder / "old", op["path"])
                        if file_hash(saved) != op["old_sha256"]:
                            raise ValueError(f"Rollback backup checksum mismatch: {saved}")
                for op in reversed(journal["operations"]):
                    target = child_path(profile.processed_path, op["path"])
                    _temporary(target, folder.name).unlink(missing_ok=True)
                    if file_hash(target) != op["old_sha256"]:
                        if op["old_sha256"] is None:
                            target.unlink(missing_ok=True)
                        else:
                            install(
                                child_path(folder / "old", op["path"]),
                                target,
                                op["old_sha256"],
                                folder.name,
                            )
        if not committed:
            report.update(status="failed", recovery="Rolled back or abandoned before publication")
            atomic_json(record, report)
        clear_transaction(profile, folder)
        results.append(
            {"run_id": folder.name, "action": "committed" if committed else "rolled_back"}
        )
    return results
