"""Bounded-memory partition fingerprints and lossless CSV serialization."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import logging
import os
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from .database import quote
from .io import sha256_file

LOG = logging.getLogger(__name__)
BOM = b"\xef\xbb\xbf"


def encode_cell(value) -> str:
    if value is None:
        return r"\N"
    if isinstance(value, bytes):
        return r"\B" + base64.b64encode(value).decode("ascii")
    text = str(value)
    return "\\" + text if text.startswith("\\") else text


def decode_cell(value: str):
    if value == r"\N":
        return None
    if value.startswith(r"\B"):
        return base64.b64decode(value[2:], validate=True)
    return value[1:] if value.startswith("\\\\") else value


class CsvEncoder:
    def __init__(self):
        self.buffer = io.StringIO(newline="")
        self.writer = csv.writer(self.buffer, lineterminator="\r\n")

    def row(self, values) -> bytes:
        self.buffer.seek(0)
        self.buffer.truncate(0)
        self.writer.writerow([encode_cell(v) for v in values])
        return (self.buffer.getvalue()[:-2] + "\n").encode("utf-8")


def rows(connection, table: str, spec: dict):
    order = ", ".join(quote(c) for c in spec["primary_key"])
    return connection.execute(f"SELECT * FROM {quote(table)} ORDER BY {order}")


def partition(table: str, row, columns: list[str]) -> str:
    if table != "Ar_Activity":
        return f"{table}/all"
    value = row[columns.index("StartLocalTime")]
    if not isinstance(value, str) or not re.match(r"^\d{4}-(0[1-9]|1[0-2])-\d{2}[ T]", value):
        raise ValueError("Unsupported StartLocalTime; expected ISO date and time")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid StartLocalTime calendar date/time") from exc
    return f"{table}/{value[:4]}/{value[5:7]}"


def fingerprint(connection, tables: dict, *, utf8_bom: bool = False) -> dict:
    """Scan all rows so old edits and deletions cannot hide below an ID watermark."""
    result = {}
    encoder = CsvEncoder()
    for table, spec in tables.items():
        LOG.info("Comparing %s", table)
        columns = [c["name"] for c in spec["columns"]]
        header = (BOM if utf8_bom else b"") + encoder.row(columns)
        partitions = {}
        total = 0
        for row in rows(connection, table, spec):
            key = partition(table, row, columns)
            if key not in partitions:
                partitions[key] = [hashlib.sha256(header), 0]
            partitions[key][0].update(encoder.row(row))
            partitions[key][1] += 1
            total += 1
            if total % 250000 == 0:
                LOG.info("Compared %s: %s rows", table, total)
        if not partitions and table != "Ar_Activity":
            partitions[f"{table}/all"] = [hashlib.sha256(header), 0]
        schema_hash = hashlib.sha256(
            json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        for key, (digest, count) in partitions.items():
            result[key] = {
                "table": table,
                "rows": count,
                "sha256": digest.hexdigest(),
                "schema_sha256": schema_hash,
            }
    return result


def changes(current: dict, previous: dict) -> dict:
    return {
        "created": sorted(set(current) - set(previous)),
        "updated": sorted(
            k
            for k in current.keys() & previous.keys()
            if any(current[k][p] != previous[k].get(p) for p in ("sha256", "schema_sha256", "rows"))
        ),
        "removed": sorted(set(previous) - set(current)),
        "unchanged": sorted(
            k
            for k in current.keys() & previous.keys()
            if all(current[k][p] == previous[k].get(p) for p in ("sha256", "schema_sha256", "rows"))
        ),
    }


def export_changed(connection, tables: dict, fingerprints: dict, selected: set, root: Path):
    """Only changed partitions are written; at most 32 output files remain open."""
    encoder = CsvEncoder()
    handles = OrderedDict()
    counts = dict.fromkeys(selected, 0)
    for key in selected:
        path = root / f"{key}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        spec = tables[fingerprints[key]["table"]]
        with path.open("xb") as stream:
            stream.write(encoder.row([c["name"] for c in spec["columns"]]))
    try:
        for table in sorted({fingerprints[k]["table"] for k in selected}):
            LOG.info("Exporting changed partitions of %s", table)
            spec = tables[table]
            columns = [c["name"] for c in spec["columns"]]
            for row in rows(connection, table, spec):
                key = partition(table, row, columns)
                if key not in selected:
                    continue
                if key not in handles:
                    if len(handles) >= 32:
                        _, old = handles.popitem(last=False)
                        old.flush()
                        os.fsync(old.fileno())
                        old.close()
                    handles[key] = (root / f"{key}.csv").open("ab")
                handles.move_to_end(key)
                handles[key].write(encoder.row(row))
                counts[key] += 1
    finally:
        for stream in handles.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
    for key in selected:
        if counts[key] != fingerprints[key]["rows"]:
            raise ValueError(f"Export row count mismatch: {key}")
        if sha256_file(root / f"{key}.csv") != fingerprints[key]["sha256"]:
            raise ValueError(f"Export checksum mismatch: {key}")
