"""Latest Raw retention, reduction safeguards and interrupted DB replacement."""

import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_fixed_layout import files

from manictime_pipeline import pipeline, raw, transaction
from manictime_pipeline.config import _validate
from manictime_pipeline.io import atomic_json, read_json, sha256_file
from manictime_pipeline.pipeline import ingest, load_current, migrate_layout, recover, verify


def change_both(profile):
    with sqlite3.connect(profile.source_path / "ManicTimeCore.db") as db:
        db.execute("UPDATE Document SET Content=Content || 'new'")
    with sqlite3.connect(profile.source_path / "ManicTimeReports.db") as db:
        db.execute("UPDATE Ar_Activity SET Name=Name || 'new'")


def test_repeated_runs_keep_one_generation_and_only_two_during_update(profile, monkeypatch):
    ingest(profile)
    old_size = sum(p.stat().st_size for p in profile.raw_directory.glob("*.db"))
    old_record = load_current(profile)
    old_record_hash = sha256_file(profile.state_path / "runs" / f"{old_record['run_id']}.json")
    original_apply = transaction.apply
    observed = []

    def observe(p, folder, journal):
        candidate_bytes = sum(
            i["bytes"]
            for i in read_json(p.state_path / "runs" / f"{folder.name}.json")["raw_capture"][
                "databases"
            ]
        )
        dbs = list(p.raw_directory.rglob("*.db"))
        observed.append(len(dbs))
        assert len(dbs) == 4
        assert sum(db.stat().st_size for db in dbs) == old_size + candidate_bytes
        assert not list(p.state_path.rglob("*.db"))  # No hidden third copy in state.
        original_apply(p, folder, journal)
        assert len(list(p.raw_directory.rglob("*.db"))) == 4

    monkeypatch.setattr(transaction, "apply", observe)
    for _ in range(3):
        change_both(profile)
        ingest(profile)
        assert {p.name for p in profile.raw_directory.iterdir()} == set(raw.DB_NAMES)
        assert verify(profile)["activity_rows"] == 3
    assert len(observed) == 3
    assert (
        sha256_file(profile.state_path / "runs" / f"{old_record['run_id']}.json") == old_record_hash
    )
    assert load_current(profile)["raw_capture"] != old_record["raw_capture"]


@pytest.mark.parametrize("phase", ["old_moved", "first_installed"])
def test_process_death_between_raw_moves_restores_both_dbs_and_csv(profile, phase):
    ingest(profile)
    before_raw, before_csv = files(profile.raw_directory), files(profile.processed_path)
    pointer = (profile.state_path / "current.json").read_bytes()
    change_both(profile)
    code = r"""
import json, os, sys
from pathlib import Path
from manictime_pipeline.config import Profile
from manictime_pipeline import pipeline, transaction
v = json.loads(sys.argv[1])
for name in ('source_path', 'raw_path', 'processed_path', 'state_path'): v[name] = Path(v[name])
p = Profile(**v)
if sys.argv[2] == 'old_moved':
    original = Path.rename
    def stop(self, target):
        result = original(self, target)
        if self.parent == p.raw_directory and self.name == 'ManicTimeCore.db': os._exit(72)
        return result
    Path.rename = stop
else:
    original = transaction.apply
    def stop(profile, folder, journal):
        original(profile, folder, {**journal, 'operations': journal['operations'][:1]})
        os._exit(72)
    transaction.apply = stop
pipeline.ingest(p)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-X",
            "utf8",
            "-c",
            code,
            json.dumps(asdict(profile), default=str),
            phase,
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 72, result.stderr.decode("utf-8", errors="replace")
    assert len(list(profile.raw_directory.rglob("*.db"))) == 4
    assert recover(profile)["runs"][0]["action"] == "rolled_back"
    assert files(profile.raw_directory) == before_raw
    assert files(profile.processed_path) == before_csv
    assert (profile.state_path / "current.json").read_bytes() == pointer
    assert verify(profile)["activity_rows"] == 3


def test_failed_extraction_discards_candidate_and_never_accumulates_raw(profile, monkeypatch):
    ingest(profile)
    before = files(profile.raw_directory)
    change_both(profile)

    def fail(*args):
        raise OSError("extract failed")

    monkeypatch.setattr(pipeline, "export_changed", fail)
    for _ in range(3):
        with pytest.raises(OSError, match="extract failed"):
            ingest(profile)
        assert files(profile.raw_directory) == before
        assert not transaction.pending(profile)
    assert verify(profile)["activity_rows"] == 3


def test_guard_blocks_reset_in_preview_and_write_without_losing_previous_data(profile, tmp_path):
    p = replace(profile, max_activity_drop_percent=10)
    ingest(p)
    before_raw, before_csv = files(p.raw_directory), files(p.processed_path)
    before_pointer = (p.state_path / "current.json").read_bytes()
    with sqlite3.connect(p.source_path / "ManicTimeReports.db") as db:
        db.execute("DELETE FROM Ar_Activity")
    before_all = files(tmp_path)
    with pytest.raises(raw.ActivityDropError, match="100.00% reduction"):
        ingest(p, True)
    assert files(tmp_path) == before_all
    with pytest.raises(raw.ActivityDropError, match="max_activity_drop_percent=10"):
        ingest(p)
    assert files(p.raw_directory) == before_raw
    assert files(p.processed_path) == before_csv
    assert (p.state_path / "current.json").read_bytes() == before_pointer
    assert verify(p)["activity_rows"] == 3
    failed = [
        read_json(f)
        for f in (p.state_path / "runs").glob("*.json")
        if read_json(f)["status"] == "failed"
    ]
    assert not failed[-1]["activity_drop_check"]["passed"]
    assert ingest(replace(p, max_activity_drop_percent=100))["activity_rows"] == 0
    assert verify(p)["activity_rows"] == 0


def test_timeline_disappearance_is_detected_even_when_total_does_not_drop(profile):
    p = replace(profile, max_activity_drop_percent=10)
    ingest(p)
    with sqlite3.connect(p.source_path / "ManicTimeReports.db") as db:
        db.execute("DELETE FROM Ar_Activity WHERE ReportId=4")
        db.execute(
            "INSERT INTO Ar_Activity SELECT 3,99,Name,GroupId,StartLocalTime,StartUtcTime,"
            "EndLocalTime,EndUtcTime,CurrentChangeSequence,Other FROM Ar_Activity LIMIT 1"
        )
    with pytest.raises(raw.ActivityDropError, match="ReportId=4"):
        ingest(p, True)


@pytest.mark.parametrize(
    "after,limit,passed",
    [(90, 10, True), (89, 10, False), (100, 0, True), (99, 0, False), (0, 100, True)],
)
def test_guard_threshold_boundary(after, limit, passed):
    old = {"rows": 100, "timelines": {"3": 100}}
    new = {"rows": after, "timelines": {"3": after}}
    assert raw.check_drop(old, new, limit)["passed"] == passed


@pytest.mark.parametrize("value", [True, False, -1, 101, "10", None, float("nan"), float("inf")])
def test_invalid_threshold_config_rejected(value):
    with pytest.raises(ValueError, match="max_activity_drop_percent"):
        _validate(
            {"schema_version": "2.1.0", "max_activity_drop_percent": value}, Path("config.yaml")
        )


def test_prior_config_stays_compatible_and_threshold_is_optional():
    for version in ["2.0.0", "2.0.9", "2.1.0", "2.1.9"]:
        assert (
            _validate({"schema_version": version}, Path("config.yaml"))["schema_version"] == version
        )


def make_v03(profile):
    result = ingest(profile)
    current = load_current(profile)
    folder = profile.raw_directory / result["run_id"]
    folder.mkdir()
    for name in raw.DB_NAMES:
        (profile.raw_directory / name).rename(folder / name)
    current.update(schema_version="2.0.0", tool_version="0.3.0", capture=result["run_id"])
    current.pop("raw_layout")
    current.pop("activity_stats")
    record = Path(result["manifest"])
    atomic_json(record, current)
    atomic_json(
        profile.state_path / "current.json",
        {
            "schema_version": "2.0.0",
            "manifest": f"runs/{result['run_id']}.json",
            "sha256": sha256_file(record),
        },
    )
    return folder, current


def test_v03_migration_keeps_identity_csv_times_and_existing_archives(profile):
    folder, previous = make_v03(profile)
    old_raw = files(folder)
    times = {p: p.stat().st_mtime_ns for p in profile.processed_path.rglob("*.csv")}
    assert verify(profile)["activity_rows"] == 3
    with pytest.raises(ValueError, match="migrate-layout"):
        ingest(profile, True)
    assert migrate_layout(profile, True)["can_migrate"]
    result = migrate_layout(profile)
    assert result["partitions"]["unchanged"] == 7
    assert load_current(profile)["dataset_id"] == previous["dataset_id"]
    assert files(folder) == old_raw
    assert all(p.stat().st_mtime_ns == mtime for p, mtime in times.items())
    assert verify(profile)["activity_rows"] == 3
    ingest(profile)
    assert len(list(profile.raw_directory.rglob("*.db"))) == 4  # Two legacy + two current.


def test_migration_failure_restores_v03_current_without_new_raw(profile, monkeypatch):
    folder, _ = make_v03(profile)
    before_raw = files(profile.raw_directory)
    pointer = (profile.state_path / "current.json").read_bytes()
    original = pipeline.atomic_json

    def fail_pointer(path, data):
        if path.name == "current.json":
            raise OSError("commit interrupted")
        return original(path, data)

    monkeypatch.setattr(pipeline, "atomic_json", fail_pointer)
    with pytest.raises(OSError, match="commit interrupted"):
        migrate_layout(profile)
    assert files(profile.raw_directory) == before_raw
    assert (profile.state_path / "current.json").read_bytes() == pointer
    assert verify(profile)["activity_rows"] == 3


def test_unowned_fixed_raw_and_manual_edit_are_never_overwritten(profile):
    profile.raw_directory.mkdir(parents=True)
    target = profile.raw_directory / "ManicTimeCore.db"
    target.write_bytes(b"unmanaged database")
    with pytest.raises(ValueError, match="Unmanaged Raw"):
        ingest(profile)
    assert target.read_bytes() == b"unmanaged database"
    target.unlink()
    ingest(profile)
    target.write_bytes(b"manual edit")
    before = files(profile.processed_path)
    with pytest.raises(ValueError, match="Raw database checksum"):
        ingest(profile)
    assert files(profile.processed_path) == before
    assert target.read_bytes() == b"manual edit"


def test_latest_raw_supports_a_live_wal_source(profile):
    writer = sqlite3.connect(profile.source_path / "ManicTimeReports.db")
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE Ar_Activity SET Name='committed'")
        writer.commit()
        writer.execute("UPDATE Ar_Activity SET Name='not committed'")
        ingest(profile)
        assert verify(profile)["activity_rows"] == 3
        with raw.readonly(profile.raw_directory / "ManicTimeReports.db") as db:
            assert db.execute("SELECT DISTINCT Name FROM Ar_Activity").fetchall() == [
                ("committed",)
            ]
        assert {p.name for p in profile.raw_directory.iterdir()} == set(raw.DB_NAMES)
    finally:
        writer.rollback()
        writer.close()


def test_verify_reconciles_activity_statistics_with_raw(profile):
    result = ingest(profile)
    manifest_path = Path(result["manifest"])
    record = read_json(manifest_path)
    record["activity_stats"]["rows"] += 1
    atomic_json(manifest_path, record)
    pointer_path = profile.state_path / "current.json"
    pointer = read_json(pointer_path)
    pointer["sha256"] = sha256_file(manifest_path)
    atomic_json(pointer_path, pointer)
    with pytest.raises(ValueError, match="activity statistics"):
        verify(profile)


def test_external_raw_sidecars_stop_ingest_without_deletion(profile):
    ingest(profile)
    before = files(profile.raw_directory)
    sidecar = profile.raw_directory / "ManicTimeReports.db-wal"
    sidecar.write_bytes(b"external SQLite state")
    with pytest.raises(ValueError, match="SQLite sidecar"):
        ingest(profile)
    assert sidecar.read_bytes() == b"external SQLite state"
    sidecar.unlink()
    assert files(profile.raw_directory) == before
    assert verify(profile)["activity_rows"] == 3
