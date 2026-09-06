"""Validate the current CSV contract and published files."""

import csv
import sys

from .config import Profile
from .export import BOM
from .io import child_path, sha256_file


def validate_csv_contract(manifest: dict) -> None:
    encoding = manifest.get("csv_contract", {}).get("encoding")
    if encoding != "UTF-8 without BOM":
        raise ValueError(f"Unsupported CSV encoding contract: {encoding!r}")


def validate_artifacts(profile: Profile, manifest: dict, count_rows: bool = False) -> None:
    validate_csv_contract(manifest)
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    for key, artifact in manifest["artifacts"].items():
        path = child_path(profile.processed_path, artifact["path"])
        if not path.is_file() or sha256_file(path) != artifact["sha256"]:
            raise ValueError(f"Missing or edited output; restore it before ingest: {path}")
        with path.open("rb") as stream:
            if stream.read(len(BOM)).startswith(BOM):
                raise ValueError(f"CSV BOM does not match its manifest: {key}")
        if count_rows:
            with path.open(encoding="utf-8", newline="") as stream:
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
