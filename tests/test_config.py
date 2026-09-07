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
        {"schema_version": "2.4.0"},
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


@pytest.mark.parametrize("layer", ["home", "cwd"])
def test_cli_discovers_config_without_options(profile, isolated, capsys, layer):
    from manictime_pipeline.cli import main

    cwd, home = isolated
    path = home / "config.yaml" if layer == "home" else cwd / ".tkn/config.yaml"
    write(
        path,
        {
            "default_profile": "desktop",
            "profiles": {
                "desktop": {"device_id": "Desktop", "source_path": str(profile.source_path)}
            },
        },
    )
    assert main(["ingest", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["profile"] == "desktop"
    assert config_show(load_config())["sources"][0]["path"] == str(path)


def test_profile_selection_precedence_without_guessing(isolated):
    cwd, home = isolated
    write(
        home / "config.yaml",
        {
            "default_profile": "home",
            "profiles": {
                n: {"device_id": n, "source_path": "source"}
                for n in ["home", "project", "explicit", "cli"]
            },
        },
    )
    write(cwd / ".tkn/config.yaml", {"default_profile": "project"})
    explicit = cwd / "override.yaml"
    write(explicit, {"default_profile": "explicit"})
    assert selected_profile(load_config()).name == "project"
    assert selected_profile(load_config(explicit)).name == "explicit"
    assert selected_profile(load_config(explicit, "cli")).name == "cli"


@pytest.mark.parametrize("selection", ["missing_default", "stale_default", "cli"])
def test_profile_error_identifies_selection_and_loaded_config(isolated, selection):
    cwd, home = isolated
    path = home / "config.yaml"
    data = {"profiles": {"desktop": {"device_id": "Desktop", "source_path": "source"}}}
    if selection != "missing_default":
        data["default_profile"] = "old-name" if selection == "stale_default" else "desktop"
    write(path, data)
    config = load_config(profile_name="typo" if selection == "cli" else None)
    with pytest.raises(ValueError) as exc:
        selected_profile(config)
    message = str(exc.value)
    assert "Available profiles: desktop" in message
    assert str(path) in message
    assert "config show" in message
    if selection == "missing_default":
        assert "default_profile is not configured" in message
    else:
        assert ("'typo'" if selection == "cli" else "'old-name'") in message
        assert ("CLI --profile" if selection == "cli" else f"selected by {path}") in message
    # Even a single available profile is not substituted for a bad or missing selection.
    assert not (home / "state").exists()


def test_no_config_error_explains_how_to_initialize(isolated):
    cwd, home = isolated
    with pytest.raises(ValueError) as exc:
        selected_profile(load_config())
    message = str(exc.value)
    assert "Loaded config files: (none)" in message
    assert "Available profiles: (none)" in message
    assert "config init" in message
    assert str(home / "config.yaml") in message


def test_missing_explicit_config_does_not_fall_back(isolated):
    cwd, home = isolated
    write(
        home / "config.yaml",
        {
            "default_profile": "desktop",
            "profiles": {"desktop": {"device_id": "Desktop", "source_path": "source"}},
        },
    )
    with pytest.raises(ValueError, match="Configuration file does not exist"):
        load_config(cwd / "missing.yaml")


def test_duplicate_device_error_identifies_both_layers(isolated):
    cwd, home = isolated
    user = home / "config.yaml"
    project = cwd / ".tkn/config.yaml"
    write(
        user,
        {
            "default_profile": "desktop",
            "profiles": {
                "desktop": {"device_id": "PC", "source_path": "source"},
            },
        },
    )
    write(
        project,
        {
            "profiles": {
                "old-desktop": {"device_id": "pc", "source_path": "source"},
            }
        },
    )
    with pytest.raises(ValueError) as exc:
        selected_profile(load_config())
    message = str(exc.value)
    for expected in ["unique", "'desktop'", "'old-desktop'", str(user), str(project)]:
        assert expected in message
