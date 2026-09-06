import csv
import sqlite3
from dataclasses import replace

import pytest

from manictime_pipeline import pipeline
from manictime_pipeline.database import backup, readonly
from manictime_pipeline.export import decode_cell
from manictime_pipeline.io import atomic_json, process_lock, read_json, sha256_file
from manictime_pipeline.pipeline import ingest, load_current, verify


def modify(profile, sql):
    with sqlite3.connect(profile.source_path / "ManicTimeReports.db") as db:
        db.execute(sql)


def test_initial_export_raw_and_roundtrip(profile):
    before = {p.name: sha256_file(p) for p in profile.source_path.glob("*.db")}
    result = ingest(profile)
    assert result["activity_rows"] == 3
    assert result["action"] == "created"
    assert verify(profile)["action"] == "verified"
    assert before == {p.name: sha256_file(p) for p in profile.source_path.glob("*.db")}
    current = load_current(profile)
    assert "Ar_ApplicationByDay" not in current["tables"]
    assert len(current["artifacts"]) == 7
    groups = current["artifacts"]["Ar_Group/all"]
    with (profile.processed_path / groups["path"]).open(encoding="utf-8", newline="") as f:
        assert decode_cell(next(csv.DictReader(f))["Icon16"]) == b"\x00\x80\xff"
    january = current["artifacts"]["Ar_Activity/2026/01"]
    with (profile.processed_path / january["path"]).open(encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f))
        assert row["Name"] == '日本語 "title",\nsecond line'
        assert decode_cell(row["Other"]) is None
    assert (profile.raw_directory / result["run_id"] / "ManicTimeCore.db").is_file()


def test_unchanged_reuses_artifacts_and_retains_new_capture(profile):
    first = ingest(profile)
    original = load_current(profile)
    second = ingest(profile)
    current = load_current(profile)
    assert second["action"] == "unchanged"
    assert second["export_rows"] == 0
    assert original["artifacts"] == current["artifacts"]
    assert original["dataset_id"] == current["dataset_id"]
    assert first["run_id"] != second["run_id"]
    assert verify(profile)["activity_rows"] == 3


def test_edits_deletes_inserts_and_month_moves_below_watermark(profile):
    ingest(profile)
    old = load_current(profile)
    modify(profile, "UPDATE Ar_Activity SET Name='old edit' WHERE ReportId=3 AND ActivityId=1")
    modify(profile, "DELETE FROM Ar_Activity WHERE ReportId=3 AND ActivityId=2")
    modify(profile, "UPDATE Ar_Activity SET StartLocalTime='2026-03-01 00:00:00' WHERE ReportId=4")
    modify(
        profile,
        "INSERT INTO Ar_Activity SELECT 3,9,'late arrival',1,"
        "'2025-12-31 23:00:00','2025-12-31 14:00:00',"
        "'2025-12-31 23:01:00','2025-12-31 14:01:00',0,NULL",
    )
    modify(profile, "UPDATE Ar_Group SET Name='Renamed' WHERE GroupId=1")
    result = ingest(profile)
    assert "Ar_Activity/2026/02" in result["changes"]["removed"]
    assert "Ar_Activity/2026/01" in result["changes"]["updated"]
    assert "Ar_Activity/2025/12" in result["changes"]["created"]
    assert "Ar_Group/all" in result["changes"]["updated"]
    assert verify(profile)["activity_rows"] == 3
    for artifact in old["artifacts"].values():
        assert sha256_file(profile.processed_path / artifact["path"]) == artifact["sha256"]


def test_dry_run_creates_no_files_or_state(profile, tmp_path):
    before = {str(p): sha256_file(p) for p in tmp_path.rglob("*") if p.is_file()}
    result = ingest(profile, dry_run=True)
    assert result["activity_rows"] == 3
    assert result["raw_databases_to_capture"] == 2
    assert not profile.raw_path.exists()
    assert not profile.processed_path.exists()
    assert not profile.state_path.exists()
    assert before == {str(p): sha256_file(p) for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("stage", ["backup", "export", "pointer"])
def test_failure_keeps_current_and_records_failure(profile, monkeypatch, stage):
    ingest(profile)
    current_path = profile.processed_path / "current.json"
    before = current_path.read_bytes()
    modify(profile, "UPDATE Ar_Activity SET Name='edited'")

    def fail(*args, **kwargs):
        raise OSError("simulated failure")

    if stage == "backup":
        monkeypatch.setattr(pipeline, "backup", fail)
    elif stage == "export":
        monkeypatch.setattr(pipeline, "export_changed", fail)
    else:
        real_atomic = pipeline.atomic_json

        def fail_pointer(path, data):
            if path.name == "current.json":
                fail()
            return real_atomic(path, data)

        monkeypatch.setattr(pipeline, "atomic_json", fail_pointer)
    with pytest.raises(OSError, match="simulated"):
        ingest(profile)
    assert current_path.read_bytes() == before
    records = [read_json(p) for p in (profile.state_path / "runs").glob("*.json")]
    assert any(r["status"] == "failed" for r in records)
    assert verify(profile)["activity_rows"] == 3


def test_failed_export_can_be_retried(profile, monkeypatch):
    real_export = pipeline.export_changed

    def fail(*args):
        raise OSError("interrupted")

    monkeypatch.setattr(pipeline, "export_changed", fail)
    with pytest.raises(OSError):
        ingest(profile)
    assert not (profile.processed_path / "current.json").exists()
    monkeypatch.setattr(pipeline, "export_changed", real_export)
    assert ingest(profile)["action"] == "created"
    assert verify(profile)["action"] == "verified"


def test_manual_edit_stops_ingest_and_verify(profile):
    ingest(profile)
    artifact = next(iter(load_current(profile)["artifacts"].values()))
    (profile.processed_path / artifact["path"]).write_text("edited", encoding="utf-8")
    for operation in [
        lambda: ingest(profile, True),
        lambda: ingest(profile),
        lambda: verify(profile),
    ]:
        with pytest.raises(ValueError, match="edited"):
            operation()


def test_schema_addition_reexports_columns(profile):
    ingest(profile)
    modify(profile, "ALTER TABLE Ar_Group ADD COLUMN NewMetadata TEXT")
    assert "Ar_Group/all" in ingest(profile)["changes"]["updated"]
    assert load_current(profile)["tables"]["Ar_Group"]["columns"][-1]["name"] == "NewMetadata"
    assert verify(profile)["action"] == "verified"


def test_invalid_timestamp_keeps_raw_and_previous_output(profile):
    ingest(profile)
    before = (profile.processed_path / "current.json").read_bytes()
    modify(profile, "UPDATE Ar_Activity SET StartLocalTime='unknown'")
    with pytest.raises(ValueError, match="StartLocalTime"):
        ingest(profile)
    assert (profile.processed_path / "current.json").read_bytes() == before
    assert len(list((profile.raw_directory).glob("*/capture.json"))) == 2


def test_source_change_refused(profile, tmp_path):
    from conftest import create_source

    ingest(profile)
    new_source = tmp_path / "other"
    create_source(new_source)
    with pytest.raises(ValueError, match="source_path"):
        ingest(replace(profile, source_path=new_source), dry_run=True)


def test_paths_cannot_overlap_source(profile):
    with pytest.raises(ValueError, match="overlap"):
        ingest(replace(profile, raw_path=profile.source_path))
    with pytest.raises(ValueError, match="overlap"):
        ingest(replace(profile, processed_path=profile.source_path / "exports"), dry_run=True)


def test_online_backup_reads_wal_but_not_uncommitted_rows(profile, tmp_path):
    source = profile.source_path / "ManicTimeReports.db"
    writer = sqlite3.connect(source)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute("UPDATE Ar_Activity SET Name='WAL committed'")
    writer.commit()
    writer.execute("UPDATE Ar_Activity SET Name='uncommitted'")
    target = tmp_path / "capture.db"
    try:
        backup(source, target, 10)
        with readonly(target) as db:
            assert db.execute("SELECT DISTINCT Name FROM Ar_Activity").fetchall() == [
                ("WAL committed",)
            ]
    finally:
        writer.rollback()
        writer.close()


def test_process_lock_and_release(profile):
    with process_lock(profile.processed_path):
        with (
            pytest.raises(ValueError, match="Another ingest"),
            process_lock(profile.processed_path),
        ):
            pass
    with process_lock(profile.processed_path):
        pass


def test_raw_corruption_detected(profile):
    result = ingest(profile)
    target = profile.raw_directory / result["run_id"] / "ManicTimeCore.db"
    with target.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        verify(profile)


def test_manifest_traversal_rejected(profile):
    profile.processed_path.mkdir(parents=True)
    atomic_json(
        profile.processed_path / "current.json",
        {
            "schema_version": "1.0.0",
            "manifest": "../../outside.json",
            "sha256": "x",
        },
    )
    with pytest.raises(ValueError, match="escapes"):
        load_current(profile)


def test_empty_activity_retains_history(profile):
    ingest(profile)
    modify(profile, "DELETE FROM Ar_Activity")
    result = ingest(profile)
    assert len(result["changes"]["removed"]) == 2
    assert result["activity_rows"] == 0
    assert verify(profile)["activity_rows"] == 0
