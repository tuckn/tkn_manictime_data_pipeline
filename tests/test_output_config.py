"""Output parent defaults, profile overrides and their visible provenance."""

import json

import pytest

from manictime_pipeline.config import config_show, load_config, selected_profile


@pytest.fixture
def config_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("manictime_pipeline.config.app_root", lambda: tmp_path / "app")
    return tmp_path


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "2.0.0", **data}), encoding="utf-8")


def test_minimal_profile_uses_default_parent_paths(config_paths):
    root = config_paths
    path = root / "settings.yaml"
    write(
        path,
        {
            "default_profile": "pc",
            "profiles": {"pc": {"device_id": "Example Current PC", "source_path": "source"}},
        },
    )
    config = load_config(path)
    profile = selected_profile(config)
    assert profile.raw_path == root / "app/data/raw"
    assert profile.raw_directory == root / "app/data/raw/Example Current PC"
    assert profile.processed_path == root / "app/data/csv/Example Current PC/pipeline-v1"
    shown = config_show(config)["effective_profiles"]["pc"]
    assert shown["winning_sources"] == {
        "raw_path": "built-in",
        "processed_data_path": "built-in",
    }
    assert shown["raw_directory"] == str(profile.raw_directory)


def test_common_and_profile_values_have_identical_parent_semantics(config_paths):
    root = config_paths
    path = root / "settings.yaml"
    write(
        path,
        {
            "default_profile": "a",
            "raw_path": "common-raw",
            "processed_data_path": "common-csv",
            "profiles": {
                "a": {"device_id": "A", "source_path": "source-a"},
                "b": {
                    "device_id": "B",
                    "source_path": "source-b",
                    "raw_path": "private-raw",
                    "processed_data_path": "private-csv",
                },
            },
        },
    )
    config = load_config(path)
    common = selected_profile(config)
    overridden = selected_profile(load_config(path, "b"))
    assert common.raw_directory == root / "common-raw/A"
    assert common.processed_path == root / "common-csv/A/pipeline-v1"
    assert overridden.raw_directory == root / "private-raw/B"
    assert overridden.processed_path == root / "private-csv/B/pipeline-v1"
    shown = config_show(config)
    assert shown["effective_profiles"]["b"]["raw_path"] == str(root / "private-raw")
    assert shown["effective_profiles"]["b"]["csv_directory"] == str(overridden.processed_path)


def test_profile_override_survives_higher_layer_common_default(config_paths):
    root = config_paths
    lower = root / "app/config.yaml"
    write(
        lower,
        {
            "default_profile": "pc",
            "profiles": {
                "pc": {"device_id": "PC", "source_path": "source", "raw_path": "override-raw"}
            },
        },
    )
    higher = root / "explicit.yaml"
    write(higher, {"raw_path": "common-raw", "processed_data_path": "common-csv"})
    config = load_config(higher)
    resolved = selected_profile(config)
    assert resolved.raw_directory == root / "override-raw/PC"
    assert resolved.processed_path == root / "common-csv/PC/pipeline-v1"
    winners = config_show(config)["effective_profiles"]["pc"]["winning_sources"]
    assert winners["raw_path"] == str(lower)
    assert winners["processed_data_path"] == str(higher)


@pytest.mark.parametrize("field", ["raw_path", "processed_data_path"])
def test_profile_overrides_are_validated_before_merge(config_paths, field):
    root = config_paths
    write(root / "app/config.yaml", {"profiles": {"pc": {field: 42}}})
    higher = root / "higher.yaml"
    write(higher, {"profiles": {"pc": {field: "valid"}}})
    with pytest.raises(ValueError, match="non-empty string"):
        load_config(higher)


def test_legacy_config_rejected_without_modification(config_paths):
    path = config_paths / "legacy.yaml"
    path.write_text('schema_version: "1.0.0"\n', encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="raw_path now names"):
        load_config(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["raw_path", "processed_data_path"])
def test_source_subfolder_cannot_be_output(config_paths, field):
    path = config_paths / "settings.yaml"
    write(
        path,
        {
            "default_profile": "pc",
            "profiles": {
                "pc": {"device_id": "PC", "source_path": "source", field: "source/output"}
            },
        },
    )
    with pytest.raises(ValueError, match="overlap"):
        selected_profile(load_config(path))


def test_template_uses_defaults_and_unique_device_once(config_paths):
    from manictime_pipeline.config import initialize_config

    initialize_config()
    p = selected_profile(load_config())
    assert p.raw_directory.parts.count(p.device_id) == 1
    assert p.processed_path.parts.count(p.device_id) == 1
