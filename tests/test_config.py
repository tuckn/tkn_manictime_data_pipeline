import json

import pytest

from manictime_pipeline.config import (
    config_show,
    initialize_config,
    load_config,
    safe_component,
    selected_profile,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "home"
    monkeypatch.setattr("manictime_pipeline.config.app_root", lambda: root)
    monkeypatch.chdir(tmp_path)
    return tmp_path, root


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "2.0.0", **data}), encoding="utf-8")


def test_layers_merge_and_show_winning_sources(isolated):
    cwd, home = isolated
    write(
        home / "config.yaml",
        {
            "default_profile": "a",
            "processed_data_path": "processed",
            "profiles": {
                "a": {"device_id": "A", "source_path": "source", "raw_path": "raw"},
                "b": {"device_id": "B", "source_path": "source-b", "raw_path": "raw-b"},
            },
        },
    )
    write(
        cwd / ".tkn/config.yaml",
        {"backup_timeout_seconds": 50, "profiles": {"a": {"raw_path": "raw-project"}}},
    )
    explicit = cwd / "explicit.yaml"
    write(explicit, {"backup_timeout_seconds": 20})
    config = load_config(explicit, "b")
    assert config["values"]["profiles"]["a"]["raw_path"] == "raw-project"
    assert config["values"]["backup_timeout_seconds"] == 20
    assert config["winning_sources"]["default_profile"] == "CLI --profile"
    assert len(config["sources"]) == 3
    assert selected_profile(config).source_path == cwd / "source-b"
    assert config_show(config)["values"]["processed_data_path"] == str(cwd / "processed")


@pytest.mark.parametrize(
    "data",
    [
        {"schema_version": "3.0.0"},
        {"schema_version": "2.1.0"},
        {"schema_version": 1},
        {"unknown": True},
        {"backup_timeout_seconds": True},
        {"backup_timeout_seconds": 0},
        {"profiles": []},
        {"profiles": {"p": {"unknown": "x"}}},
        {"profiles": {"p": {"source_path": 1}}},
    ],
)
def test_invalid_lower_layer_is_not_hidden(isolated, data):
    cwd, home = isolated
    write(home / "config.yaml", data)
    write(cwd / "higher.yaml", {"backup_timeout_seconds": 10})
    with pytest.raises(ValueError):
        load_config(cwd / "higher.yaml")


def test_patch_version_and_missing_version(isolated):
    cwd, home = isolated
    write(home / "config.yaml", {"schema_version": "2.0.5"})
    assert load_config()["sources"][0]["schema_version"] == "2.0.5"
    (home / "config.yaml").write_text("default_profile: pc\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        load_config()


@pytest.mark.parametrize(
    "text",
    [
        'schema_version: "2.0.0"\ndefault_profile: a\ndefault_profile: b\n',
        'schema_version: "2.0.0"\nprofiles:\n  a:\n    device_id: x\n    device_id: y\n',
    ],
)
def test_duplicate_yaml_keys(isolated, text):
    cwd, home = isolated
    home.mkdir()
    (home / "config.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_config()


@pytest.mark.parametrize("name", ["../x", "CON", "NUL.txt", "a/b", "x:y", "a.", "a ", ""])
def test_windows_unsafe_component(name):
    with pytest.raises(ValueError):
        safe_component(name)


def test_config_init_and_edited_protection(isolated):
    cwd, home = isolated
    assert initialize_config(True)["action"] == "would_create"
    assert not home.exists()
    assert initialize_config()["action"] == "created"
    assert initialize_config()["action"] == "unchanged"
    target = home / "config.yaml"
    target.write_text("my edits", encoding="utf-8")
    with pytest.raises(ValueError, match="preserved"):
        initialize_config()
    assert target.read_text() == "my edits"


def test_config_show_no_writes(isolated):
    cwd, home = isolated
    assert config_show(load_config())["sources"] == []
    assert not home.exists()


def test_duplicate_devices_are_rejected(isolated):
    cwd, home = isolated
    write(
        home / "config.yaml",
        {
            "default_profile": "a",
            "processed_data_path": "out",
            "profiles": {
                name: {"device_id": device, "source_path": name, "raw_path": name + "-raw"}
                for name, device in [("a", "PC"), ("b", "pc")]
            },
        },
    )
    with pytest.raises(ValueError, match="unique"):
        selected_profile(load_config())


def test_cli_json_and_diagnostics(profile, isolated, capsys):
    from manictime_pipeline.cli import main

    cwd, home = isolated
    config = cwd / "config.yaml"
    write(
        config,
        {
            "default_profile": "test",
            "processed_data_path": str(cwd / "cli-output"),
            "profiles": {
                "test": {
                    "device_id": "PC",
                    "source_path": str(profile.source_path),
                    "raw_path": str(profile.raw_path),
                }
            },
        },
    )
    assert main(["--config", str(config), "ingest", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["action"] == "dry_run"
    assert "[SUCCESS]" in captured.err
    assert main(["inspect", "--config", str(config), "--profile", "missing"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[ERROR]" in captured.err


def test_cli_config_init_rejects_profile(isolated, capsys):
    from manictime_pipeline.cli import main

    assert main(["config", "init", "--profile", "a"]) == 1
    assert "[ERROR]" in capsys.readouterr().err


def test_cli_quiet_verbose_conflict_across_subcommands(isolated):
    from manictime_pipeline.cli import main

    with pytest.raises(SystemExit):
        main(["--quiet", "config", "show", "--verbose"])
