"""Public CLI contract using synthetic inputs; no personal config or external services."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_reports import report_config as report_config

from manictime_pipeline import cli, review
from manictime_pipeline.output import config_lines, public_result
from manictime_pipeline.pipeline import ingest


@pytest.fixture
def cli_config(profile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("manictime_pipeline.cli.app_root", lambda: tmp_path / "home")
    monkeypatch.setattr("manictime_pipeline.config.app_root", lambda: tmp_path / "home")
    config = tmp_path / "config.yaml"
    config.write_text(
        json.dumps(
            {
                "schema_version": "2.3.0",
                "default_profile": "test",
                "raw_path": str(profile.raw_path),
                "processed_data_path": str(profile.processed_path.parent),
                "state_path": str(profile.state_path.parent),
                "report_path": str(tmp_path / "reports"),
                "profiles": {
                    "test": {
                        "device_id": profile.device_id,
                        "source_path": str(profile.source_path),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return config


def snapshot(root):
    return {
        p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else None
        for p in root.rglob("*")
    }


@pytest.mark.parametrize("json_output", [False, True])
def test_every_finite_command_and_dry_run(cli_config, profile, tmp_path, capsys, json_output):
    flags = ["--json"] if json_output else []

    def run(*args):
        assert cli.main([*flags, "--config", str(cli_config), *args]) == 0
        captured = capsys.readouterr()
        assert "\x1b" not in captured.out
        if json_output:
            result = json.loads(captured.out)
            assert result["status"] == "success" and result["exit_code"] == 0
            return result
        assert "status=success" in captured.out.splitlines()
        return captured.out

    before = snapshot(tmp_path)
    run("config", "list")
    run("inspect")
    run("ingest", "--dry-run")
    run("recover", "--dry-run")
    run("rules", "init", "--dry-run")
    assert snapshot(tmp_path) == before
    run("rules", "init")
    preserved = run("rules", "init")
    if json_output:
        assert preserved["counts"]["preserved"] == 4
        assert all(item["path"] for item in preserved["items"])
    run("ingest")
    first = snapshot(profile.processed_path)
    run("verify")
    repeated = run("ingest")
    assert snapshot(profile.processed_path) == first
    if json_output:
        assert repeated["action"] == "unchanged"
        assert repeated["partitions"]["unchanged"] > 0
        assert "unchanged" not in repeated["changes"]
        assert "--full-output" in repeated["details_hint"]
    else:
        assert "action=unchanged" in repeated.splitlines()
        assert "details_omitted.changes.unchanged=" in repeated
    before = snapshot(tmp_path)
    full = run("ingest", "--dry-run", "--full-output")
    assert snapshot(tmp_path) == before
    if json_output:
        assert len(full["changes"]["unchanged"]) == full["partitions"]["unchanged"]
        assert "details_omitted" not in full
    else:
        assert "changes.unchanged[0]=" in full
    run("recover")


@pytest.mark.parametrize("json_output", [False, True])
def test_config_init_and_protected_file(tmp_path, monkeypatch, capsys, json_output):
    root = tmp_path / "user"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("manictime_pipeline.cli.app_root", lambda: root)
    monkeypatch.setattr("manictime_pipeline.config.app_root", lambda: root)
    flags = ["--json"] if json_output else []
    assert cli.main(["config", *flags, "init", "--dry-run"]) == 0
    capsys.readouterr()
    assert not root.exists()
    assert cli.main(["config", "init", *flags]) == 0
    captured = capsys.readouterr()
    if json_output:
        assert json.loads(captured.out)["counts"] == {"created": 1}
    else:
        assert f"path={root / 'config.yaml'}" in captured.out.splitlines()
    target = root / "config.yaml"
    target.write_text("edited", encoding="utf-8")
    assert cli.main([*flags, "config", "init"]) == 1
    captured = capsys.readouterr()
    assert target.read_text("utf-8") == "edited"
    if json_output:
        assert json.loads(captured.out)["errors"][0]["targets"][0]["path"] == str(target)
    else:
        assert f"errors[0].targets[0].path={target}" in captured.out.splitlines()


@pytest.mark.parametrize(
    "args",
    [
        ["--json", "config", "list"],
        ["config", "--json", "list"],
        ["config", "list", "--json"],
        ["--json", "config", "--json", "list", "--json"],
        ["--json", "rules", "init", "--dry-run"],
        ["rules", "--json", "init", "--dry-run"],
        ["rules", "init", "--dry-run", "--json"],
    ],
)
def test_json_survives_every_subcommand_level(cli_config, capsys, args):
    assert cli.main(["--config", str(cli_config), *args]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"


@pytest.mark.parametrize(
    "args",
    [
        ["--json"],
        ["--json", "unknown"],
        ["config", "unknown", "--json"],
        ["config", "list", "--bad", "--json"],
        ["review", "--port", "bad", "--json"],
        ["--quiet", "config", "list", "--verbose", "--json"],
    ],
)
def test_json_argument_errors(args, capsys):
    assert cli.main(args) == 2
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "failed" and data["exit_code"] == 2
    assert data["errors"][0]["type"] == "ArgumentError"
    assert "[ERROR]" in captured.err and "\x1b" not in captured.out


@pytest.mark.parametrize(
    "body",
    ['schema_version: "99.0.0"', "schema_version: [", 'schema_version: "2.3.0"\nunknown: secret'],
)
def test_json_config_errors_identify_path(cli_config, capsys, body):
    cli_config.write_text(body, encoding="utf-8")
    assert cli.main(["--json", "config", "list", "--config", str(cli_config)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["errors"][0]["targets"] == [{"role": "config", "path": str(cli_config)}]


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize(
    "command",
    [
        "ingest",
        "verify",
        "inspect",
        "recover",
        "build-report",
        "rules init",
        "review",
        "config init",
    ],
)
def test_runtime_failure_and_interrupt_preserve_format(
    cli_config, profile, capsys, monkeypatch, command, json_output
):
    function = {
        "build-report": "build_report",
        "rules init": "initialize_rules",
        "config init": "initialize_config",
        "inspect": "inspect_source",
    }.get(command, command)
    monkeypatch.setattr(cli, "selected_profile", lambda c: profile)
    target = review if command == "review" else cli
    flags = ["--json"] if json_output else []
    for exception, code in [
        (RuntimeError("failed after some work"), 1),
        (KeyboardInterrupt(), 130),
    ]:

        def fail(*args, error=exception, **kwargs):
            raise error

        monkeypatch.setattr(target, function, fail)
        args = [*command.split(), *flags]
        if command != "config init":
            args += ["--config", str(cli_config)]
        assert cli.main(args) == code
        captured = capsys.readouterr()
        if json_output:
            result = json.loads(captured.out)
            assert result["exit_code"] == code
            assert result["status"] == ("interrupted" if code == 130 else "failed")
            assert result["errors"][0]["targets"]
        else:
            assert f"exit_code={code}" in captured.out.splitlines()
        assert "[SUCCESS]" not in captured.err


def test_corrupt_csv_identifies_exact_target(cli_config, profile, capsys):
    ingest(profile)
    csv = next(profile.processed_path.rglob("*.csv"))
    csv.write_text("edited", encoding="utf-8")
    assert cli.main(["verify", "--config", str(cli_config), "--json"]) == 1
    error = json.loads(capsys.readouterr().out)["errors"][0]
    assert error["targets"] == [{"role": "csv", "path": str(csv)}]
    assert csv.read_text("utf-8") == "edited"


def test_unmanaged_csv_all_targets_are_available(cli_config, profile, capsys):
    profile.processed_path.mkdir(parents=True)
    for index in range(8):
        (profile.processed_path / f"unmanaged-{index}.csv").write_text("keep", encoding="utf-8")
    assert cli.main(["ingest", "--dry-run", "--json", "--config", str(cli_config)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert len(result["errors"][0]["targets"]) == 8


@pytest.mark.parametrize("json_output", [False, True])
def test_partial_rules_failure_retains_progress_and_exit_code(
    cli_config, capsys, monkeypatch, json_output
):
    original = Path.open

    def fail_second(self, *args, **kwargs):
        if self.name == "site_rules.csv" and args and args[0] == "xb":
            raise PermissionError(13, "test denial", str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_second)
    flags = ["--json"] if json_output else []
    assert cli.main(["rules", "init", "--config", str(cli_config), *flags]) == 1
    captured = capsys.readouterr()
    if json_output:
        result = json.loads(captured.out)
        assert result["status"] == "failed" and result["exit_code"] == 1
        assert result["counts"]["processed_items"] == 1
        assert result["counts"]["actions"] == {"created": 1}
        assert Path(result["processed_items"][0]["path"]).is_file()
        assert result["errors"][0]["targets"][0]["path"].endswith("site_rules.csv")
    else:
        assert "counts.processed_items=1" in captured.out
        assert "counts.actions.created=1" in captured.out
    assert "[SUCCESS]" not in captured.err


def test_report_failure_after_device_processing(report_config, monkeypatch, capsys):
    from manictime_pipeline import reports

    monkeypatch.setattr(cli, "load_config", lambda *a: report_config)

    def fail(*args):
        raise ValueError("test failure before publishing combined page")

    monkeypatch.setattr(reports, "combined_payload", fail)
    assert cli.main(["build-report", "--no-open", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and result["counts"]["processed_items"] == 1
    assert result["processed_items"][0]["device_id"]
    assert "publication" in result["progress_note"]


def test_review_serving_interrupt_retains_exit_zero(cli_config, monkeypatch, capsys):
    from unittest.mock import MagicMock

    server = MagicMock()
    server.server_port = 8767
    server.serve_forever.side_effect = KeyboardInterrupt
    context = MagicMock()
    context.__enter__.return_value = server
    monkeypatch.setattr(review, "make_server", lambda *a: context)
    monkeypatch.setattr(review, "build_report", lambda *a, **k: None)
    assert cli.main(["review", "--no-open", "--config", str(cli_config), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "stopped"


def test_interrupt_while_parsing_preserves_json(monkeypatch, capsys):
    def stop(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.Parser, "parse_args", stop)
    assert cli.main(["--json", "config", "list"]) == 130
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted" and result["command"] is None


@pytest.mark.parametrize("json_output", [False, True])
def test_report_and_review_final_results(report_config, monkeypatch, capsys, json_output):
    monkeypatch.setattr(cli, "load_config", lambda *a: report_config)
    flags = ["--json"] if json_output else []
    before = snapshot(Path(report_config["values"]["report_path"]).parent)
    assert cli.main(["build-report", "--dry-run", *flags]) == 0
    captured = capsys.readouterr()
    assert snapshot(Path(report_config["values"]["report_path"]).parent) == before
    if json_output:
        result = json.loads(captured.out)
        assert result["counts"]["devices"] == 1 and result["dry_run"]
    else:
        assert "dry_run=true" in captured.out.splitlines()
    monkeypatch.setattr(review, "review", lambda *a: None)
    assert cli.main(["review", "--no-open", *flags]) == 0
    captured = capsys.readouterr()
    if json_output:
        assert json.loads(captured.out)["action"] == "stopped"
    else:
        assert "action=stopped" in captured.out.splitlines()


@pytest.mark.parametrize("json_output", [False, True])
def test_verbosity_and_tty_never_change_results(cli_config, capsys, monkeypatch, json_output):
    flags = ["--json"] if json_output else []
    outputs = []
    for tty in [False, True]:
        monkeypatch.setattr(sys.stdout, "isatty", lambda value=tty: value)
        for verbosity in [[], ["--quiet"], ["--verbose"]]:
            assert (
                cli.main(["--config", str(cli_config), "config", "list", *flags, *verbosity]) == 0
            )
            captured = capsys.readouterr()
            outputs.append(captured.out)
            assert (captured.err == "") == (verbosity == ["--quiet"])
    assert len(set(outputs)) == 1


def test_output_preserves_long_paths_tuples_and_keys():
    path = "C:\\" + "長い名前\\" * 80 + "file.csv"
    result = {"path": path, "range": ("first", "last"), "line\nbreak": "value\x1b"}
    lines = config_lines(result)
    assert lines[0] == "path=" + path
    assert lines[1:3] == ["range[0]=first", "range[1]=last"]
    assert lines[3] == r"line\nbreak=value\u001b"


def test_full_output_does_not_mutate_python_result():
    result = {"action": "unchanged", "changes": {"created": [], "unchanged": ["a", "b"]}}
    original = json.dumps(result)
    summary = public_result("ingest", result, full_output=False)
    full = public_result("ingest", result, full_output=True)
    assert summary["details_omitted"] == {"changes.unchanged": 2}
    assert full["changes"]["unchanged"] == ["a", "b"]
    assert json.dumps(result) == original


def test_real_pipe_redirect_help_version_and_outside_cwd(tmp_path):
    env = {
        **os.environ,
        "USERPROFILE": str(tmp_path / "home"),
        "HOME": str(tmp_path / "home"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def run(*args, stdout=subprocess.PIPE):
        return subprocess.run(
            [sys.executable, "-m", "manictime_pipeline", *args],
            cwd=tmp_path,
            env=env,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=20,
        )

    text = run("config", "list")
    assert text.returncode == 0 and "status=success" in text.stdout
    data = run("--json", "config", "list")
    assert data.returncode == 0 and json.loads(data.stdout)["status"] == "success"
    output = tmp_path / "redirect.json"
    with output.open("w", encoding="utf-8") as stream:
        assert run("config", "list", "--json", stdout=stream).returncode == 0
    assert json.loads(output.read_text("utf-8")) == json.loads(data.stdout)
    with output.open("w", encoding="utf-8") as stream:
        assert run("config", "list", stdout=stream).returncode == 0
    assert output.read_text("utf-8") == text.stdout
    assert "--json" in run("--json", "--help").stdout
    assert run("--json", "--version").stdout.strip() == "0.9.0"
    assert not (tmp_path / "home").exists()


def test_separator_does_not_select_json(capsys):
    assert cli.main(["config", "list", "--", "--json"]) == 2
    assert "status=failed" in capsys.readouterr().out
