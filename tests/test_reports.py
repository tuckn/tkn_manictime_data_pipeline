"""Report contracts: interval boundaries, evidence, dry runs and safe publication."""

import csv
import io
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from manictime_pipeline import reports
from manictime_pipeline.config import config_show, load_config
from manictime_pipeline.pipeline import ingest
from manictime_pipeline.report_data import (
    IntervalIndex,
    classify,
    interval,
    lookup_term,
    period_ids,
    pieces,
    rules_from,
)


@pytest.fixture
def report_config(profile, monkeypatch, tmp_path):
    db = sqlite3.connect(profile.source_path / "ManicTimeReports.db")
    db.executescript("""
    DELETE FROM Ar_Activity;
    ALTER TABLE Ar_Activity ADD COLUMN RelatedActivityId INT;
    ALTER TABLE Ar_Group ADD COLUMN Key TEXT;
    ALTER TABLE Ar_Group ADD COLUMN Other TEXT;
    UPDATE Ar_Group SET Name='Google Chrome', Key='chrome.exe;google chrome',
        Other='{"fileName":"chrome.exe","fullPath":"C:/Chrome Dev/chrome.exe"}';
    INSERT INTO Ar_Timeline VALUES (9, 'usage', 'ManicTime/ComputerUsage');
    INSERT INTO Ar_Group (ReportId, GroupId, Name, Key) VALUES
      (9,1,'Active','ManicTime/Active'),(9,2,'Away','ManicTime/Away');
    """)
    # A foreground interval crosses local midnight and the calendar-year boundary.
    entries = [
        (9, 1, 1, "2025-12-31 14:59:00", "2025-12-31 15:00:30", "Active", None),
        (9, 2, 2, "2025-12-31 15:00:30", "2025-12-31 15:01:00", "Away", None),
        (3, 1, 1, "2025-12-31 14:59:00", "2025-12-31 15:01:00", "Title - Google Chrome", None),
        (
            4,
            1,
            None,
            "2025-12-31 14:59:30",
            "2025-12-31 15:00:45",
            "https://dictionary.cambridge.org/dictionary/english/consequence?q=Consequences",
            1,
        ),
        (9, 3, 1, "2026-01-01 01:00:00", "2026-01-01 01:02:00", "Active", None),
        (
            3,
            2,
            1,
            "2026-01-01 01:00:00",
            "2026-01-01 01:02:00",
            "</script><img src=x onerror=alert(1)> - Google Chrome",
            None,
        ),
        (
            4,
            2,
            None,
            "2026-01-01 01:00:00",
            "2026-01-01 01:01:00",
            "https://ejje.weblio.jp/content/consequence",
            2,
        ),
        (
            4,
            3,
            None,
            "2026-01-01 01:01:00",
            "2026-01-01 01:02:00",
            "https://dictionary.cambridge.org/dictionary/english/consequence",
            2,
        ),
        (9, 4, 1, "2026-01-02 01:00:00", "2026-01-02 01:01:00", "Active", None),
        (3, 3, 1, "2026-01-02 01:00:00", "2026-01-02 01:01:00", "Google Chrome", None),
        (
            4,
            4,
            None,
            "2026-01-02 01:00:00",
            "2026-01-02 01:01:00",
            "https://www.oxfordlearnersdictionaries.com/definition/english/consequence_1",
            3,
        ),
    ]
    tz = ZoneInfo("Asia/Tokyo")
    for rid, aid, group, start, end, name, parent in entries:
        local_start = (
            datetime.fromisoformat(start)
            .replace(tzinfo=UTC)
            .astimezone(tz)
            .strftime("%Y-%m-%d %H:%M:%S")
        )
        local_end = (
            datetime.fromisoformat(end)
            .replace(tzinfo=UTC)
            .astimezone(tz)
            .strftime("%Y-%m-%d %H:%M:%S")
        )
        db.execute(
            "INSERT INTO Ar_Activity VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, aid, name, group, local_start, start, local_end, end, 1, "{}", parent),
        )
    db.commit()
    db.close()
    ingest(profile)
    monkeypatch.setattr(
        reports, "report_cutoff", lambda tz: datetime(2026, 1, 3, tzinfo=tz).timestamp()
    )
    values = {
        "default_profile": profile.name,
        "raw_path": str(profile.raw_path),
        "processed_data_path": str(profile.processed_path.parent),
        "state_path": str(profile.state_path.parent),
        "backup_timeout_seconds": 10,
        "max_activity_drop_percent": 100,
        "report_path": str(tmp_path / "reports"),
        "report_timezone": "Asia/Tokyo",
        "report_lookup_gap_minutes": 30,
        "profiles": {
            profile.name: {"device_id": profile.device_id, "source_path": str(profile.source_path)}
        },
    }
    return {"values": values, "winning_sources": {}, "sources": []}


def snapshot_data(config):
    root = Path(config["values"]["report_path"])
    manifest = json.loads((root / "report-manifest.json").read_text("utf-8"))
    entry = manifest["devices"]["Test PC"]
    folder = root / entry["path"]
    return folder, json.loads((folder / "data.json").read_text("utf-8"))


def test_intervals_and_calendar_boundaries():
    index = IntervalIndex([(0, 50), (20, 60), (80, 100)])
    assert index.seconds(30, 90) == 40
    assert index.seconds(60, 80) == 0
    assert period_ids("2025-12-31") == ("all", "2025", "2025-12", "2026-W01")
    tz = ZoneInfo("America/New_York")
    start = datetime(2025, 11, 2, 0, tzinfo=tz).timestamp()
    end = datetime(2025, 11, 3, 0, tzinfo=tz).timestamp()
    assert sum(b - a for _, _, a, b in pieces(start, end, tz)) == 25 * 3600


def test_midnight_split_active_intersection_and_dictionary_sessions(report_config):
    result = reports.build_report(report_config)
    assert result["devices"][0]["action"] == "generated"
    folder, data = snapshot_data(report_config)
    assert data["periods"]["2025"]["apps"][0]["foreground_seconds"] == 60
    assert data["periods"]["2025"]["apps"][0]["active_seconds"] == 60
    assert data["periods"]["2026"]["apps"][0]["foreground_seconds"] == 240
    assert data["periods"]["2026"]["apps"][0]["active_seconds"] == 210
    assert data["periods"]["2026-W01"]["apps"][0]["active_seconds"] == 270
    assert data["periods"]["all"]["terms"][0]["days"] == 3
    assert data["periods"]["all"]["terms"][0]["sessions"] == 3
    assert data["periods"]["all"]["terms"][0]["intervals"] == 4
    assert data["lookups"][0]["search_term"] == "Consequences"
    assert all(r["channel"] == "Unknown" for r in data["periods"]["all"]["apps"])
    page = (folder / "index.html").read_text("utf-8")
    assert "</script><img" not in page
    assert r"\u003c/script>" in page
    assert (folder / "applications.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    second = reports.build_report(report_config)
    assert second["devices"][0]["action"] == "unchanged"


def test_dry_run_does_not_create_or_change_any_files(report_config, tmp_path):
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = reports.build_report(report_config, dry_run=True)
    after = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
    assert result["devices"][0]["action"] == "would_generate"
    assert not Path(report_config["values"]["report_path"]).exists()


def test_edited_report_is_preserved(report_config):
    reports.build_report(report_config)
    folder, _ = snapshot_data(report_config)
    (folder / "index.html").write_text("user edited", encoding="utf-8")
    with pytest.raises(ValueError, match="Edited generated report"):
        reports.build_report(report_config)
    assert (folder / "index.html").read_text() == "user edited"


def test_missing_artifact_rebuilt(report_config):
    reports.build_report(report_config)
    folder, _ = snapshot_data(report_config)
    (folder / "domains.csv").unlink()
    assert reports.build_report(report_config)["devices"][0]["action"] == "generated"
    assert (folder / "domains.csv").exists()


def test_interrupted_entry_point_can_be_retried(report_config, monkeypatch):
    reports.build_report(report_config)
    report_config["values"]["report_lookup_gap_minutes"] = 20
    original = reports.os.replace
    root = Path(report_config["values"]["report_path"])

    def fail_once(src, dst):
        if Path(dst) == root / "index.html":
            raise OSError("simulated publication interruption")
        return original(src, dst)

    monkeypatch.setattr(reports.os, "replace", fail_once)
    with pytest.raises(OSError, match="simulated"):
        reports.build_report(report_config)
    monkeypatch.setattr(reports.os, "replace", original)
    reports.build_report(report_config)
    assert not (root / ".index.pending.html").exists()
    assert reports.sha256_file(root / "index.html") == reports.root_manifest(root)["index_sha256"]


def test_source_corruption_and_pending_ingest_stop_report(report_config, tmp_path):
    source = (
        Path(report_config["values"]["processed_data_path"]) / "Test PC/Ar_Activity/2026/01.csv"
    )
    source.write_bytes(source.read_bytes() + b"bad\n")
    with pytest.raises(ValueError):
        reports.build_report(report_config)
    assert not Path(report_config["values"]["report_path"]).exists()


def test_report_destination_cannot_overlap_input(report_config):
    report_config["values"]["report_path"] = report_config["values"]["processed_data_path"]
    with pytest.raises(ValueError, match="overlap"):
        reports.build_report(report_config, dry_run=True)


def test_rules_and_channel_evidence(tmp_path):
    group = {
        "Name": "Google Chrome",
        "Key": "chrome.exe;google chrome",
        "Other": '{"fullPath":"C:/Chrome Dev/chrome.exe"}',
    }
    assert classify(group, "page - Google Chrome", "PC", "2026-01-01", [])[2] == "Unknown"
    assert classify(group, "page - Google Chrome Beta", "PC", "2026-01-01", [])[2] == "Beta"
    path = tmp_path / "rules.csv"
    path.write_text(
        "device_id,valid_from,valid_to,match_field,pattern,category,family,channel,purpose\n"
        "PC,2026-01-01,2026-02-01,key,chrome.exe;*,Browser,Chrome,Dev,Research\n",
        encoding="utf-8",
    )
    rules = rules_from(path)
    assert classify(group, "", "PC", "2026-01-02", rules)[2:] == ("Dev", "Research")
    assert classify(group, "", "Other PC", "2026-01-02", rules)[2] == "Unknown"
    assert classify(group, "", "PC", "2025-12-31", rules)[2] == "Unknown"
    assert lookup_term("https://ejje.weblio.jp/", "Weblio") is None
    assert (
        lookup_term("https://dictionary.cambridge.org/dictionary/english/look-up", "")[1]
        == "look-up"
    )


def test_csv_formula_safety():
    output = reports.csv_bytes([{"name": "=HYPERLINK(1)", "seconds": -1}], ["name", "seconds"])
    row = next(csv.DictReader(io.StringIO(output.decode("utf-8-sig"))))
    assert row == {"name": "'=HYPERLINK(1)", "seconds": "-1"}


def test_cutoff_and_invalid_intervals():
    from collections import Counter

    quality = Counter()
    cutoff = datetime(2026, 1, 2, tzinfo=UTC).timestamp()
    clipped = interval(
        {"StartUtcTime": "2026-01-01T23:59:00Z", "EndUtcTime": "2026-01-02T00:01:00Z"},
        cutoff,
        quality,
    )
    assert clipped == (cutoff - 60, cutoff)
    assert (
        interval(
            {"StartUtcTime": "2026-01-02T00:00:00Z", "EndUtcTime": "2026-01-02T00:01:00Z"},
            cutoff,
            quality,
        )
        is None
    )
    assert interval({"StartUtcTime": "invalid", "EndUtcTime": "invalid"}, cutoff, quality) is None
    assert quality == {"excluded_today_or_future_rows": 1, "invalid_timestamp_rows": 1}


def test_relocated_identical_csv_can_be_reported(report_config, profile):
    from manictime_pipeline.io import atomic_json, read_json, sha256_file

    pointer = profile.state_path / "current.json"
    current = read_json(pointer)
    path = profile.state_path / current["manifest"]
    source = read_json(path)
    source.update(profile="old-name", processed_root="C:/old/csv", source_path="C:/old/source")
    atomic_json(path, source)
    current["sha256"] = sha256_file(path)
    atomic_json(pointer, current)
    assert (
        reports.build_report(report_config, dry_run=True)["devices"][0]["action"]
        == "would_generate"
    )


def test_all_pcs_and_single_pc_update_preserve_other_links(report_config, profile):
    from dataclasses import replace

    second = replace(
        profile,
        name="second",
        device_id="Other PC",
        processed_path=profile.processed_path.parent / "Other PC",
        state_path=profile.state_path.parent / "second",
    )
    ingest(second)
    report_config["values"]["profiles"]["second"] = {
        "device_id": second.device_id,
        "source_path": str(second.source_path),
    }
    result = reports.build_report(report_config)
    assert len(result["devices"]) == 2
    report_config["values"]["report_lookup_gap_minutes"] = 15
    result = reports.build_report(report_config, profile_name=profile.name)
    assert len(result["devices"]) == 1
    root = Path(report_config["values"]["report_path"])
    assert set(reports.root_manifest(root)["devices"]) == {"Test PC", "Other PC"}
    assert b"Other PC" in (root / "index.html").read_bytes()


def test_only_latest_and_previous_generations_remain(report_config):
    root = Path(report_config["values"]["report_path"])
    for gap in (10, 20, 30):
        report_config["values"]["report_lookup_gap_minutes"] = gap
        reports.build_report(report_config)
    assert len(list((root / "devices/Test PC").iterdir())) == 2


def test_pending_ingest_blocks_report(report_config, profile):
    (profile.state_path / "transactions/pending").mkdir(parents=True)
    with pytest.raises(ValueError, match="Unfinished Raw/CSV update"):
        reports.build_report(report_config, dry_run=True)


def test_config_defaults_and_report_validation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("manictime_pipeline.config.app_root", lambda: tmp_path / "app")
    shown = config_show(load_config())
    assert shown["values"]["report_path"] == str(tmp_path / "app/reports")
    path = tmp_path / "settings.yaml"
    path.write_text('schema_version: "2.2.0"\nreport_lookup_gap_minutes: 0\n')
    with pytest.raises(ValueError, match="report_lookup_gap_minutes"):
        load_config(path)


@pytest.mark.parametrize("options,opens", [([], 1), (["--no-open"], 0), (["--dry-run"], 0)])
def test_cli_report_browser_and_stdout(report_config, monkeypatch, capsys, options, opens):
    from manictime_pipeline import cli

    monkeypatch.setattr(cli, "load_config", lambda *args: report_config)
    launched = []
    monkeypatch.setattr(
        cli.webbrowser, "open", lambda *args, **kwargs: launched.append(args) or True
    )
    assert cli.main(["build-report", *options]) == 0
    assert len(launched) == opens
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Report:" in captured.err
