"""Latest Raw snapshot validation and activity-loss detection."""

from pathlib import Path

from .database import DB_NAMES, check_snapshot_sidecars, quick_check
from .database import readonly_snapshot as readonly
from .io import child_path, sha256_file


def directory(manifest: dict) -> Path:
    if manifest.get("raw_layout") != "latest":
        raise ValueError("Unsupported Raw layout; expected latest")
    return Path(manifest["raw_root"])


def database_items(capture: dict) -> dict:
    items = capture["databases"]
    if len(items) != len(DB_NAMES) or {i["file"] for i in items} != set(DB_NAMES):
        raise ValueError("Raw capture must contain exactly the two required databases")
    return {i["file"]: i for i in items}


def validate(manifest: dict, *, integrity: bool = False) -> None:
    root = directory(manifest)
    for name, item in database_items(manifest["raw_capture"]).items():
        path = child_path(root, name)
        check_snapshot_sidecars(path)
        if (
            not path.is_file()
            or path.stat().st_size != item["bytes"]
            or sha256_file(path) != item["sha256"]
        ):
            raise ValueError(f"Raw database checksum mismatch: {path}")
        if integrity:
            with readonly(path) as connection:
                quick_check(connection)


def statistics(connection) -> dict:
    timelines = {
        str(report): count
        for report, count in connection.execute(
            "SELECT ReportId, COUNT(*) FROM Ar_Activity GROUP BY ReportId"
        )
    }
    return {"rows": sum(timelines.values()), "timelines": timelines}


def previous_statistics(previous: dict | None) -> dict | None:
    if previous is None:
        return None
    return previous["activity_stats"]


def check_drop(previous: dict | None, current: dict, limit: float) -> dict:
    checks = []
    if previous:
        counts = [("all", previous["rows"], current["rows"])]
        counts += [
            (f"ReportId={key}", value, current["timelines"].get(key, 0))
            for key, value in previous["timelines"].items()
        ]
        for scope, before, after in counts:
            if before and after < before:
                # Compare unrounded values; exactly the configured limit is allowed.
                drop = 100 * (before - after) / before
                if drop > limit:
                    checks.append(
                        {
                            "scope": scope,
                            "previous_rows": before,
                            "current_rows": after,
                            "drop_percent": drop,
                        }
                    )
    return {"max_activity_drop_percent": limit, "passed": not checks, "violations": checks}


class ActivityDropError(ValueError):
    def __init__(self, check: dict):
        self.check = check
        details = "; ".join(
            f"{v['scope']}: {v['previous_rows']} -> {v['current_rows']} "
            f"({v['drop_percent']:.2f}% reduction)"
            for v in check["violations"]
        )
        super().__init__(
            f"Activity reduction exceeds max_activity_drop_percent="
            f"{check['max_activity_drop_percent']}: {details}. "
            "Previous Raw and CSV are retained. Inspect the source; for an intended "
            "reduction, use an explicit config override and preview it first."
        )
