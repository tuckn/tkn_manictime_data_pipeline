"""Current CSV contract validation."""

import json
from pathlib import Path

import pytest

from manictime_pipeline.io import atomic_json, read_json, sha256_file
from manictime_pipeline.pipeline import ingest, verify


def test_manifest_cannot_claim_another_csv_encoding(profile):
    result = ingest(profile)
    path = Path(result["manifest"])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["csv_contract"]["encoding"] = "UTF-16"
    atomic_json(path, manifest)
    pointer = read_json(profile.state_path / "current.json")
    pointer["sha256"] = sha256_file(path)
    atomic_json(profile.state_path / "current.json", pointer)
    with pytest.raises(ValueError, match="Unsupported CSV encoding"):
        verify(profile)


def test_manifest_bom_claim_is_checked(profile):
    result = ingest(profile)
    path = Path(result["manifest"])
    manifest = read_json(path)
    manifest["csv_contract"]["encoding"] = "UTF-8 with BOM"
    atomic_json(path, manifest)
    pointer = read_json(profile.state_path / "current.json")
    pointer["sha256"] = sha256_file(path)
    atomic_json(profile.state_path / "current.json", pointer)
    with pytest.raises(ValueError, match="Unsupported CSV encoding"):
        verify(profile)


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0"])
def test_unsupported_state_is_rejected_without_writes(profile, version):
    from manictime_pipeline.io import read_json

    ingest(profile)
    pointer = profile.state_path / "current.json"
    index = read_json(pointer)
    index["schema_version"] = version
    atomic_json(pointer, index)
    before = {
        p: p.read_bytes()
        for root in [profile.state_path, profile.raw_directory, profile.processed_path]
        for p in root.rglob("*")
        if p.is_file()
    }
    with pytest.raises(ValueError, match="Unsupported current.json schema_version"):
        ingest(profile, True)
    with pytest.raises(ValueError, match="Unsupported current.json schema_version"):
        verify(profile)
    assert all(p.read_bytes() == content for p, content in before.items())


def test_unmanaged_csv_in_any_subfolder_blocks_publication(profile):
    ingest(profile)
    extra = profile.processed_path / "pipeline-v1/runs/old/Ar_Group/all.csv"
    extra.parent.mkdir(parents=True)
    extra.write_text("preserve this file", encoding="utf-8")
    with pytest.raises(ValueError, match="Unmanaged CSV"):
        ingest(profile, True)
    assert extra.read_text(encoding="utf-8") == "preserve this file"


def test_migration_command_is_not_available(capsys):
    from manictime_pipeline.cli import parser

    assert "migrate-layout" not in parser().format_help()
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(["migrate-layout"])
    assert exc.value.code == 2


def test_bom_bytes_are_rejected_even_with_matching_hash(profile):
    from manictime_pipeline.export import BOM

    result = ingest(profile)
    record = Path(result["manifest"])
    manifest = read_json(record)
    artifact = next(iter(manifest["artifacts"].values()))
    path = profile.processed_path / artifact["path"]
    path.write_bytes(BOM + path.read_bytes())
    artifact["sha256"] = sha256_file(path)
    atomic_json(record, manifest)
    pointer = read_json(profile.state_path / "current.json")
    pointer["sha256"] = sha256_file(record)
    atomic_json(profile.state_path / "current.json", pointer)
    with pytest.raises(ValueError, match="BOM does not match"):
        verify(profile)
