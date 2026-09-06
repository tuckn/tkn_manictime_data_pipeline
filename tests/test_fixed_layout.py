"""Stable paths and process-termination recovery using real SQLite fixtures."""

import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, replace

import pytest

from manictime_pipeline import pipeline, transaction
from manictime_pipeline.io import read_json, sha256_file
from manictime_pipeline.pipeline import ingest, load_current, recover, verify


def files(root):
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in root.rglob("*") if p.is_file()}


def mutate(profile):
    with sqlite3.connect(profile.source_path / "ManicTimeReports.db") as db:
        db.execute("DELETE FROM Ar_Activity WHERE StartLocalTime LIKE '2026-01%'")
        db.execute("UPDATE Ar_Activity SET Name='changed'")
        db.execute(
            "INSERT INTO Ar_Activity SELECT ReportId, 99, Name, GroupId, "
            "'2026-03-01 01:00:00', StartUtcTime, EndLocalTime, EndUtcTime, "
            "CurrentChangeSequence, Other FROM Ar_Activity LIMIT 1"
        )


def crash_ingest(profile, boundary):
    code = r"""
import json, os, sys
from pathlib import Path
from manictime_pipeline.config import Profile
from manictime_pipeline import pipeline, transaction
values = json.loads(sys.argv[1])
for name in ('source_path', 'raw_path', 'processed_path', 'state_path'):
    values[name] = Path(values[name])
profile = Profile(**values)
boundary = sys.argv[2]
if boundary == 'commit':
    transaction.clear_transaction = lambda *args: os._exit(71)
else:
    original = transaction.apply
    def stop(profile, folder, journal):
        original(profile, folder, {**journal, 'operations': journal['operations'][:int(boundary)]})
        os._exit(71)
    transaction.apply = stop
pipeline.ingest(profile)
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
            str(boundary),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 71, result.stderr.decode("utf-8", errors="replace")


def test_data_folders_only_contain_data_and_unchanged_csv_is_not_rewritten(profile):
    first = ingest(profile)
    csvs = list(profile.processed_path.rglob("*"))
    assert all(p.suffix == ".csv" for p in csvs if p.is_file())
    assert set(files(profile.raw_directory / first["run_id"])) == {
        "ManicTimeCore.db",
        "ManicTimeReports.db",
    }
    assert (profile.processed_path / "Ar_Activity/2026/01.csv").is_file()
    assert (profile.processed_path / "Ar_Group/all.csv").is_file()
    times = {p: p.stat().st_mtime_ns for p in csvs if p.is_file()}
    record = load_current(profile)
    assert record["raw_capture"]["databases"][0]["sha256"]
    assert record["effective_config"]["processed_data_path"] == str(profile.processed_path.parent)
    ingest(profile)
    assert times == {p: p.stat().st_mtime_ns for p in times}
    assert not transaction.pending(profile)


@pytest.mark.parametrize("boundary", [1, 3])
def test_process_death_rolls_back_deleted_updated_and_created_partitions(profile, boundary):
    ingest(profile)
    before = files(profile.processed_path)
    pointer = (profile.state_path / "current.json").read_bytes()
    mutate(profile)
    crash_ingest(profile, boundary)
    for command in (lambda: verify(profile), lambda: ingest(profile, True)):
        with pytest.raises(ValueError, match="recover"):
            command()
    state_before = files(profile.state_path)
    assert len(recover(profile, True)["pending_runs"]) == 1
    assert files(profile.state_path) == state_before
    assert recover(profile)["runs"][0]["action"] == "rolled_back"
    assert files(profile.processed_path) == before
    assert (profile.state_path / "current.json").read_bytes() == pointer
    assert verify(profile)["activity_rows"] == 3
    assert ingest(profile)["action"] == "updated"
    assert not (profile.processed_path / "Ar_Activity/2026/01.csv").exists()
    assert (profile.processed_path / "Ar_Activity/2026/03.csv").exists()
    assert verify(profile)["activity_rows"] == 3


def test_process_death_after_commit_only_cleans_staging(profile):
    ingest(profile)
    mutate(profile)
    crash_ingest(profile, "commit")
    current = load_current(profile)
    before = files(profile.processed_path)
    assert recover(profile)["runs"][0]["action"] == "committed"
    assert load_current(profile) == current
    assert files(profile.processed_path) == before
    assert verify(profile)["action"] == "verified"


def test_recovery_preserves_manual_edits_and_can_resume(profile):
    ingest(profile)
    before = files(profile.processed_path)
    mutate(profile)
    crash_ingest(profile, 3)
    target = profile.processed_path / "Ar_Activity/2026/02.csv"
    intended = target.read_bytes()
    target.write_bytes(b"manual edit")
    edited = files(profile.processed_path)
    with pytest.raises(ValueError, match="edited during recovery"):
        recover(profile)
    assert files(profile.processed_path) == edited
    target.write_bytes(intended)
    assert recover(profile)["action"] == "recovered"
    assert files(profile.processed_path) == before


def test_ingest_automatically_recovers_before_the_next_update(profile):
    ingest(profile)
    mutate(profile)
    crash_ingest(profile, 1)
    assert ingest(profile)["action"] == "updated"
    assert verify(profile)["action"] == "verified"
    assert any(read_json(p).get("recovery") for p in (profile.state_path / "runs").glob("*.json"))


def test_state_failure_stops_before_any_raw_or_csv_write(profile, monkeypatch):
    def fail(*args):
        raise OSError("state is read-only")

    monkeypatch.setattr(pipeline, "atomic_json", fail)
    with pytest.raises(OSError, match="state is read-only"):
        ingest(profile)
    assert not profile.raw_directory.exists()
    assert not profile.processed_path.exists()


def test_replacing_a_csv_failure_rolls_back_completed_replacements(profile, monkeypatch):
    ingest(profile)
    before = files(profile.processed_path)
    mutate(profile)
    original = transaction.install
    failed = False

    def fail_once(source, destination, expected, run_id):
        nonlocal failed
        if "/new/" in source.as_posix() and not failed:
            failed = True
            raise PermissionError("file is open in another application")
        return original(source, destination, expected, run_id)

    monkeypatch.setattr(transaction, "install", fail_once)
    with pytest.raises(PermissionError, match="file is open"):
        ingest(profile)
    assert files(profile.processed_path) == before
    assert verify(profile)["action"] == "verified"


def test_unowned_files_and_lost_state_are_not_adopted(profile):
    ingest(profile)
    before = files(profile.processed_path)
    with pytest.raises(ValueError, match="Unmanaged"):
        ingest(replace(profile, state_path=profile.state_path.parent / "lost"), True)
    assert files(profile.processed_path) == before
    extra = profile.processed_path / "Ar_Activity/2026/12.csv"
    extra.write_bytes(b"not managed by this pipeline")
    with pytest.raises(ValueError, match="Unmanaged"):
        ingest(profile)
    assert extra.read_bytes() == b"not managed by this pipeline"


def test_output_binding_and_cross_profile_lock(profile):
    ingest(profile)
    with pytest.raises(ValueError, match="processed_root"):
        ingest(replace(profile, processed_path=profile.processed_path.parent / "other"), True)
    other = replace(profile, name="other", state_path=profile.state_path.parent / "other")
    with transaction.dataset_lock(profile):
        with pytest.raises(ValueError, match="Another ingest"), transaction.dataset_lock(other):
            pass


def test_recovery_never_removes_an_unrecognized_transaction_folder(profile):
    folder = profile.state_path / "transactions/user-files"
    folder.mkdir(parents=True)
    (folder / "keep.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown transaction"):
        recover(profile)
    assert (folder / "keep.txt").read_text(encoding="utf-8") == "preserve"


def test_missing_run_record_blocks_recovery_without_removing_csv_or_backups(profile):
    ingest(profile)
    mutate(profile)
    crash_ingest(profile, 3)
    run = transaction.pending(profile)[0].name
    record = profile.state_path / "runs" / f"{run}.json"
    content = record.read_bytes()
    record.unlink()
    csv_before = files(profile.processed_path)
    backups_before = files(profile.state_path / "transactions")
    with pytest.raises(ValueError, match="missing run record"):
        recover(profile)
    assert files(profile.processed_path) == csv_before
    assert files(profile.state_path / "transactions") == backups_before
    record.write_bytes(content)
    assert recover(profile)["action"] == "recovered"
