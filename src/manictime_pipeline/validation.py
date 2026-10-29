"""Validate the current CSV contract and published files."""

import csv
import sys

from .config import Profile
from .diagnostics import target_error
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
            raise target_error(
                ValueError(f"Missing or edited output; restore it before ingest: {path}"),
                path,
                "csv",
            )
        with path.open("rb") as stream:
            if stream.read(len(BOM)).startswith(BOM):
                raise target_error(
                    ValueError(f"CSV BOM does not match its manifest: {key}"), path, "csv"
                )
        if count_rows:
            with path.open(encoding="utf-8", newline="") as stream:
                reader = csv.reader(stream)
                header = next(reader)
                expected = [c["name"] for c in manifest["tables"][artifact["table"]]["columns"]]
                if header != expected:
                    raise target_error(ValueError(f"CSV header mismatch: {key}"), path, "csv")
                count = 0
                for row in reader:
                    if len(row) != len(header):
                        raise target_error(
                            ValueError(f"CSV column count mismatch: {key}"), path, "csv"
                        )
                    count += 1
            if count != artifact["rows"]:
                raise target_error(ValueError(f"CSV row count mismatch: {key}"), path, "csv")
