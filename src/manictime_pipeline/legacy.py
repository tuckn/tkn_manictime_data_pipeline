"""Read-only validation of v0.1/v0.2 output for explicit layout migration."""

from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path

from .config import Profile
from .database import DB_NAMES, quick_check, schema
from .database import readonly_snapshot as readonly
from .export import BOM, changes, fingerprint
from .io import child_path, read_json, sha256_file

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
