"""Read-only SQLite access and online backup; no external sqlite3 executable."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .io import sha256_file

DB_NAMES = ("ManicTimeCore.db", "ManicTimeReports.db")
REQUIRED_KEYS = {
    "Ar_Activity": ["ReportId", "ActivityId"],
    "Ar_Group": ["ReportId", "GroupId"],
    "Ar_CommonGroup": ["CommonId"],
    "Ar_Timeline": ["ReportId"],
}
LOG = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def check_snapshot_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists():
            raise ValueError(
                f"Unexpected SQLite sidecar beside Raw; close external readers/writers "
                f"and inspect before continuing: {path}{suffix}"
            )


@contextmanager
def readonly(path: Path, *, snapshot: bool = False):
    uri = path.resolve().as_uri() + "?mode=ro"
    if snapshot:
        check_snapshot_sidecars(path)
        # Only completed backup copies are immutable while open. Never use for a live source.
        uri += "&immutable=1"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        yield connection
    finally:
        connection.close()


def readonly_snapshot(path: Path):
    """Read a completed, standalone Raw DB without creating WAL/shared-memory files."""
    return readonly(path, snapshot=True)


def quick_check(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite quick_check failed; keep the source and inspect its integrity")


def schema(connection: sqlite3.Connection) -> dict:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    tables = {}
    for name, sql in rows:
        if not re.fullmatch(r"Ar_[A-Za-z0-9_]+", name):
            continue
        if re.search(r"By(?:Hour|Day|Year)$", name) or name == "Ar_TimelineSummary":
            continue
        cols = connection.execute(f"PRAGMA table_info({quote(name)})").fetchall()
        keys = [r[1] for r in sorted(cols, key=lambda r: r[5]) if r[5]]
        if not keys:
            raise ValueError(f"Unsupported table without a declared primary key: {name}")
        tables[name] = {
            "columns": [{"name": r[1], "type": r[2], "not_null": bool(r[3])} for r in cols],
            "primary_key": keys,
            "sql": sql,
        }
    for name, keys in REQUIRED_KEYS.items():
        if name not in tables or tables[name]["primary_key"] != keys:
            raise ValueError(f"Unsupported Reports schema: expected {name} primary key {keys}")
    required_activity = {
        "StartLocalTime",
        "StartUtcTime",
        "EndLocalTime",
        "EndUtcTime",
        "Name",
        "GroupId",
    }
    actual = {c["name"] for c in tables["Ar_Activity"]["columns"]}
    if required_activity - actual:
        raise ValueError(f"Missing Ar_Activity columns: {sorted(required_activity - actual)}")
    return tables


def backup(source: Path, destination: Path, timeout: int) -> dict:
    started_at = now()
    deadline = time.monotonic() + timeout
    last_log = time.monotonic()

    def progress(status, remaining, total):
        nonlocal last_log
        if time.monotonic() > deadline:
            raise TimeoutError(f"Backup timed out after {timeout}s: {source.name}")
        if time.monotonic() - last_log >= 10:
            LOG.info("Capturing %s: %s/%s pages", source.name, total - remaining, total)
            last_log = time.monotonic()

    with readonly(source) as src:
        journal_mode = src.execute("PRAGMA journal_mode").fetchone()[0]
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst, pages=1024, progress=progress, sleep=0.05)
            quick_check(dst)
        finally:
            dst.close()
    # Flush the completed snapshot before its capture manifest is published.
    with destination.open("rb+") as stream:
        import os

        os.fsync(stream.fileno())
    return {
        "file": source.name,
        "source_path": str(source.resolve()),
        "started_at": started_at,
        "completed_at": now(),
        "source_journal_mode": journal_mode,
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "method": "sqlite_online_backup",
    }


def inspect_source(directory: Path) -> dict:
    databases = {}
    for name in DB_NAMES:
        with readonly(directory / name) as connection:
            entry = {
                "bytes": (directory / name).stat().st_size,
                "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
            }
            if name == "ManicTimeReports.db":
                tables = schema(connection)
                entry["tables"] = {
                    name: {
                        **spec,
                        "rows": connection.execute(
                            f"SELECT count(*) FROM {quote(name)}"
                        ).fetchone()[0],
                    }
                    for name, spec in tables.items()
                }
                entry["activity_range"] = connection.execute(
                    "SELECT min(StartLocalTime), max(EndLocalTime) FROM Ar_Activity"
                ).fetchone()
            databases[name] = entry
    return databases
