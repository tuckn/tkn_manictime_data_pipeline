"""End-to-end upgrade from v0.1 captures and BOM CSVs without losing old evidence."""

import json
from pathlib import Path

import pytest

from manictime_pipeline import pipeline
from manictime_pipeline.database import readonly, schema
from manictime_pipeline.export import BOM, fingerprint
from manictime_pipeline.io import atomic_json, read_json, sha256_file
from manictime_pipeline.pipeline import ingest, load_current, verify


@pytest.fixture
def legacy_dataset(profile, tmp_path):
    # Build a small valid legacy dataset with actual BOM bytes and the v0.1 Raw layout.
    result = ingest(profile)
    current = load_current(profile)
    raw = Path(result["raw_capture"])
    old_raw = profile.raw_directory / "Raw" / result["run_id"]
    assert raw.resolve().is_relative_to(tmp_path.resolve())
    assert old_raw.resolve().is_relative_to(tmp_path.resolve())
    old_raw.parent.mkdir()
    raw.rename(old_raw)
    capture = read_json(old_raw / "capture.json")
    capture["tool_version"] = "0.1.0"
    atomic_json(old_raw / "capture.json", capture)
    current["tool_version"] = "0.1.0"
    current["raw_root"] = str(old_raw.parent.resolve())
    current["capture_sha256"] = sha256_file(old_raw / "capture.json")
    current["csv_contract"]["encoding"] = "UTF-8 with BOM"
    with readonly(old_raw / "ManicTimeReports.db") as db:
        legacy_fingerprints = fingerprint(db, schema(db), utf8_bom=True)
    for key, artifact in current["artifacts"].items():
        csv_path = profile.processed_path / artifact["path"]
        csv_path.write_bytes(BOM + csv_path.read_bytes())
        artifact["sha256"] = legacy_fingerprints[key]["sha256"]
        assert sha256_file(csv_path) == artifact["sha256"]
    manifest_path = Path(result["manifest"])
    atomic_json(manifest_path, current)
    atomic_json(
        profile.processed_path / "current.json",
        {
            "schema_version": "1.0.0",
            "manifest": manifest_path.relative_to(profile.processed_path).as_posix(),
            "sha256": sha256_file(manifest_path),
        },
    )
    preserved = {
        p: sha256_file(p)
        for p in list(old_raw.rglob("*")) + list(manifest_path.parent.rglob("*"))
        if p.is_file()
    }
    return current, preserved


def test_upgrade_verifies_legacy_and_publishes_bom_free_versions(profile, legacy_dataset):
    old, preserved = legacy_dataset
    assert verify(profile)["action"] == "verified"
    preview = ingest(profile, dry_run=True)
    assert preview["partitions"]["updated"] == len(old["artifacts"])
    assert preview["partitions"]["unchanged"] == 0
    result = ingest(profile)
    current = load_current(profile)
    assert current["dataset_id"] == old["dataset_id"]
    assert current["previous_run_id"] == old["run_id"]
    assert current["raw_root"] == str(profile.raw_directory.resolve())
    assert current["csv_contract"]["encoding"] == "UTF-8 without BOM"
    assert result["partitions"]["updated"] == len(old["artifacts"])
    for key, artifact in current["artifacts"].items():
        payload = (profile.processed_path / artifact["path"]).read_bytes()
        assert not payload.startswith(BOM)
        assert (
            payload.decode("utf-8")
            .splitlines()[0]
            .startswith(current["tables"][artifact["table"]]["columns"][0]["name"])
        )
        assert b"\r\n" not in payload  # Fixture data contains LF, no embedded CRLF.
        assert artifact["rows"] == old["artifacts"][key]["rows"]
    assert {p: sha256_file(p) for p in preserved} == preserved
    assert verify(profile)["action"] == "verified"
    assert ingest(profile)["action"] == "unchanged"


def test_failed_upgrade_keeps_legacy_dataset_usable(profile, legacy_dataset, monkeypatch):
    old, preserved = legacy_dataset
    pointer = (profile.processed_path / "current.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError("injected upgrade failure")

    monkeypatch.setattr(pipeline, "export_changed", fail)
    with pytest.raises(OSError, match="injected"):
        ingest(profile)
    assert (profile.processed_path / "current.json").read_bytes() == pointer
    assert {p: sha256_file(p) for p in preserved} == preserved
    assert verify(profile)["action"] == "verified"


def test_manifest_cannot_claim_another_csv_encoding(profile):
    result = ingest(profile)
    path = Path(result["manifest"])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["csv_contract"]["encoding"] = "UTF-16"
    atomic_json(path, manifest)
    pointer = read_json(profile.processed_path / "current.json")
    pointer["sha256"] = sha256_file(path)
    atomic_json(profile.processed_path / "current.json", pointer)
    with pytest.raises(ValueError, match="Unsupported CSV encoding"):
        verify(profile)


def test_manifest_bom_claim_is_checked(profile):
    result = ingest(profile)
    path = Path(result["manifest"])
    manifest = read_json(path)
    manifest["csv_contract"]["encoding"] = "UTF-8 with BOM"
    atomic_json(path, manifest)
    pointer = read_json(profile.processed_path / "current.json")
    pointer["sha256"] = sha256_file(path)
    atomic_json(profile.processed_path / "current.json", pointer)
    with pytest.raises(ValueError, match="BOM does not match"):
        verify(profile)
