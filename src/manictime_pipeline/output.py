"""CLI-only result presentation; stored records and Python return values are unchanged."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import PurePath
from typing import Any


def text_value(value: Any) -> str:
    if isinstance(value, PurePath):
        value = str(value)
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False)
    escapes = {"\r": "\\r", "\n": "\\n", "\t": "\\t"}
    return "".join(
        escapes.get(char, f"\\u{ord(char):04x}")
        if ord(char) < 32 or 127 <= ord(char) <= 159 or char in "\u2028\u2029"
        else char
        for char in value
    )


def config_lines(value: Any, prefix: str = "") -> list[str]:
    """Flatten result data, including SQLite tuples, into copyable key=value lines."""
    if isinstance(value, dict) and value:
        return [
            line
            for key, item in value.items()
            for line in config_lines(item, f"{prefix}.{key}" if prefix else str(key))
        ]
    if isinstance(value, (list, tuple)) and value:
        return [
            line
            for index, item in enumerate(value)
            for line in config_lines(item, f"{prefix}[{index}]")
        ]
    return [f"{text_value(prefix)}={text_value(value)}"]


def public_result(command: str, result: dict | list, *, full_output: bool) -> dict:
    """Add CLI metadata and counts, optionally summarizing unchanged partitions only."""
    data = dict(result) if isinstance(result, dict) else {"items": result}
    counts = {}
    if command == "rules init":
        counts = {"items": len(result), **Counter(item["action"] for item in result)}
    elif command == "config init":
        counts = {result["action"]: 1}
    elif command == "config list":
        counts = {
            "config_files": len(result["sources"]),
            "profiles": len(result["values"]["profiles"]),
        }
    elif command == "inspect":
        counts = {
            "databases": len(result),
            "tables": sum(len(v.get("tables", {})) for v in result.values()),
        }
    elif command == "build-report":
        counts = {
            "devices": len(result["devices"]),
            **Counter(d["action"] for d in result["devices"]),
        }
    elif command == "recover":
        counts = (
            {"pending_runs": len(result["pending_runs"])}
            if "pending_runs" in result
            else {"runs": len(result["runs"]), **Counter(r["action"] for r in result["runs"])}
        )
    if not full_output and data.get("changes", {}).get("unchanged"):
        data["changes"] = dict(data["changes"])
        unchanged = data["changes"].pop("unchanged")
        data["details_omitted"] = {"changes.unchanged": len(unchanged)}
        data["details_hint"] = "Use --full-output to include every unchanged partition"
    return {
        "status": "success",
        "command": command,
        "exit_code": 0,
        **data,
        **({"counts": counts} if counts else {}),
    }


def emit(result: dict, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print("\n".join(config_lines(result)))


def failure_result(command: str | None, exc: BaseException, code: int, targets: list[dict]) -> dict:
    """Expose paths as fields; never try to parse paths out of exception messages."""
    specific = list(getattr(exc, "cli_targets", []))
    for name in ("filename", "filename2"):
        path = getattr(exc, name, None)
        if path is not None:
            specific.append({"role": "file", "path": str(path)})
    error = {
        "type": type(exc).__name__,
        "reason": str(exc) or "Interrupted",
        "targets": specific or targets,
    }
    if hasattr(exc, "check"):
        error["activity_drop_check"] = exc.check
    result = {
        "status": "interrupted" if code == 130 else "failed",
        "command": command,
        "exit_code": code,
        "counts": {"errors": 1},
        "errors": [error],
    }
    processed = getattr(exc, "cli_processed_items", None)
    if processed:
        result["processed_items"] = processed
        result["counts"]["processed_items"] = len(processed)
        result["counts"]["actions"] = dict(Counter(item["action"] for item in processed))
        result["progress_note"] = (
            "Items processed before failure; this does not confirm publication or rollback"
        )
    return result
