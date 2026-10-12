import json

import pytest

from manictime_pipeline.cli import config_lines, main


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "home"
    monkeypatch.setattr("manictime_pipeline.config.app_root", lambda: root)
    return tmp_path, root


def test_config_lines_copyable_paths_and_nested_values():
    assert config_lines(
        {
            "path": r"C:\Users\ExampleUser\ManicTime",
            "items": [{"enabled": True}, False, None, 10, 2.5],
            "empty_list": [],
            "empty_map": {},
            "text": '日本語 "quoted"=value',
            "controls": "a\r\n\tb\x00\x1b\x7f\x85\u2028\u2029",
        }
    ) == [
        r"path=C:\Users\ExampleUser\ManicTime",
        "items[0].enabled=true",
        "items[1]=false",
        "items[2]=null",
        "items[3]=10",
        "items[4]=2.5",
        "empty_list=[]",
        "empty_map={}",
        'text=日本語 "quoted"=value',
        r"controls=a\r\n\tb\u0000\u001b\u007f\u0085\u2028\u2029",
    ]


@pytest.mark.parametrize("json_output", [False, True])
def test_config_list_without_config_is_readonly(isolated, capsys, json_output):
    cwd, root = isolated
    args = ["config", "list", *(["--json"] if json_output else [])]
    assert main(args) == 0
    captured = capsys.readouterr()
    assert captured.err == "[INFO] Showing resolved configuration\n"
    if json_output:
        result = json.loads(captured.out)
        assert result["sources"] == []
        assert result["values"]["profiles"] == {}
        assert result["selected_profile"] is None
        assert result["migration_applied"] is False
    else:
        assert "sources=[]" in captured.out.splitlines()
        assert "values.profiles={}" in captured.out.splitlines()
        assert "selected_profile=null" in captured.out.splitlines()
        assert "migration_applied=false" in captured.out.splitlines()
    assert not root.exists()
    assert list(cwd.iterdir()) == []


def test_config_list_layers_profile_json_parity_and_no_writes(isolated, capsys):
    cwd, root = isolated
    user = root / "config.yaml"
    project = cwd / ".tkn/config.yaml"
    explicit = cwd / "override.yaml"
    for path, data in [
        (
            user,
            {
                "default_profile": "pc",
                "profiles": {
                    "pc": {"device_id": "Example PC", "source_path": "missing-source"},
                },
            },
        ),
        (project, {"backup_timeout_seconds": 50}),
        (explicit, {"backup_timeout_seconds": 20, "processed_data_path": "csv"}),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": "2.0.5", **data}), encoding="utf-8")

    def snapshot():
        return {p.relative_to(cwd): p.read_bytes() if p.is_file() else None for p in cwd.rglob("*")}

    before = snapshot()
    args = ["--config", str(explicit), "config", "list", "--profile", "pc"]
    assert main([*args, "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == "[INFO] Showing resolved configuration\n"
    assert result["selected_profile"] == "pc"
    assert result["winning_sources"]["default_profile"] == "CLI --profile"
    assert result["winning_sources"]["backup_timeout_seconds"] == str(explicit)
    assert [source["path"] for source in result["sources"]] == [
        str(user),
        str(project),
        str(explicit),
    ]
    assert all(source["schema_version"] == "2.0.5" for source in result["sources"])
    assert result["schema_version"] == "2.3.0"
    assert result["values"]["processed_data_path"] == str(cwd / "csv")
    assert result["effective_profiles"]["pc"]["csv_directory"] == str(cwd / "csv/Example PC")
    assert main(args) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == config_lines(result)
    assert captured.err == "[INFO] Showing resolved configuration\n"
    assert snapshot() == before


@pytest.mark.parametrize("option", ["--quiet", "--verbose"])
@pytest.mark.parametrize("json_output", [False, True])
def test_config_list_verbosity(isolated, capsys, option, json_output):
    assert main([option, "config", "list", *(["--json"] if json_output else [])]) == 0
    captured = capsys.readouterr()
    assert captured.out
    assert captured.err == (
        "" if option == "--quiet" else "[INFO] Showing resolved configuration\n"
    )


def test_config_list_help_removed_show_and_error(isolated, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["config", "list", "--help"])
    assert exc.value.code == 0
    assert "--json" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["config", "show"])
    assert exc.value.code == 2
    capsys.readouterr()
    assert main(["config", "list", "--config", "missing.yaml"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[ERROR]" in captured.err
