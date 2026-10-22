import copy
import csv
import http.client
import json
import threading
from pathlib import Path

import pytest
import yaml
from test_reports import report_config as report_config
from test_reports import snapshot_data

from manictime_pipeline import reports, review
from manictime_pipeline.application_rules import (
    EMPTY_RULES,
    ROLES,
    classify_application,
    matches,
    parse_application_rules,
)
from manictime_pipeline.report_data import classify


def rules_with(section, match, patch):
    rules = copy.deepcopy(EMPTY_RULES)
    rules[section].append({"id": "example", "match": match, "set": patch})
    return rules


def test_shared_chrome_metadata_never_implies_channel_or_engagement():
    group = {
        "Key": "chrome.exe;google chrome",
        "Other": json.dumps({"fullPath": "C:/Chrome Beta/chrome.exe"}),
    }
    rules = rules_with(
        "engagement", {"family": "Chrome", "channel": "Beta"}, {"engagement": "focus"}
    )
    for title, expected in [
        ("Google Chrome", ("Unknown", "unknown")),
        ("Title - Google Chrome Beta", ("Beta", "focus")),
    ]:
        result, _ = classify_application(
            classify(group, title, "PC", "2026-01-01", []), group, title, "PC", "2026-01-01", rules
        )
        assert (result[2], result[4]) == expected


def test_classification_then_engagement_first_match_partial_patch():
    rules = rules_with(
        "classifications",
        {"file_name": "APP.EXE", "device_id": "Test*"},
        {"family": "Editor", "category": "Desktop app"},
    )
    rules["engagement"] = [
        {"id": "specific", "match": {"family": "Editor"}, "set": {"engagement": "focus"}},
        {"id": "fallback", "match": {"file_name": "*"}, "set": {"engagement": "distracted"}},
    ]
    parse_application_rules(yaml.safe_dump(rules))
    result, evidence = classify_application(
        ("Unknown", "App", "Beta", "Memo"),
        {"Key": "app.exe;app"},
        "Title",
        "Test PC",
        "2026-01-01",
        rules,
    )
    assert result == ("Desktop app", "Editor", "Beta", "Memo", "focus")
    assert evidence == {"classification_rule": "example", "engagement_rule": "specific"}


def test_date_and_folder_boundaries():
    rule = {
        "match": {"group_path_prefix": "C:\\Apps\\Private\\"},
        "valid_from": "2026-01-01",
        "valid_to": "2026-01-02",
    }
    for path, day, expected in [
        ("c:/apps/private/tool.exe", "2026-01-01", True),
        ("C:/Apps/Private/sub/tool.exe", "2026-01-02", True),
        ("C:/Apps/Private-other/tool.exe", "2026-01-01", False),
        ("C:/Apps/Private/tool.exe", "2025-12-31", False),
        ("", "2026-01-01", False),
    ]:
        assert matches(rule, {"group_path_prefix": path}, day) is expected


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(extra=True),
        lambda r: r["engagement"][0].update(match={}),
        lambda r: r["engagement"][0].update(match={"title_regex": ".*"}),
        lambda r: r["engagement"][0].update(set={"engagement": "productive"}),
        lambda r: r["engagement"][0].update(valid_from="2026-13-01"),
        lambda r: r["engagement"][0].update(valid_from="2026-02-01", valid_to="2026-01-01"),
        lambda r: r["engagement"][0].update(match={"group_path_prefix": "C:/Apps/*"}),
        lambda r: r["engagement"].append(copy.deepcopy(r["engagement"][0])),
    ],
)
def test_invalid_rules_rejected(mutate):
    rules = rules_with("engagement", {"family": "Editor"}, {"engagement": "focus"})
    mutate(rules)
    with pytest.raises(ValueError):
        parse_application_rules(yaml.safe_dump(rules))


def test_exact_hour_partition_csv_and_rule_invalidation(report_config):
    rules = rules_with("engagement", {"title": "Title*"}, {"engagement": "focus"})
    rules["engagement"].append(
        {"id": "remaining", "match": {"title": "*"}, "set": {"engagement": "distracted"}}
    )
    target = Path(report_config["values"]["application_overrides_path"])
    target.write_text(yaml.safe_dump(rules), encoding="utf-8")
    reports.build_report(report_config)
    folder, data = snapshot_data(report_config)
    # Foreground spans midnight; Away time must not become classified Active time.
    assert data["daily"]["2025-12-31"]["engagement_hourly"]["focus"][23] == 60
    assert data["daily"]["2026-01-01"]["engagement_hourly"]["focus"][0] == 30
    assert data["daily"]["2026-01-01"]["engagement_hourly"]["distracted"][10] == 120
    for row in data["daily"].values():
        assert (
            sum(sum(row["engagement_hourly"][role]) for role in ROLES) == row["app_active_seconds"]
        )
    assert {r["engagement_rule"] for r in data["inventory"]} == {"example", "remaining"}
    with (folder / "daily.csv").open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
        assert float(row["focus_seconds"]) == 60
    target.write_text(yaml.safe_dump(EMPTY_RULES), encoding="utf-8")
    result = reports.build_report(report_config)
    assert not result["devices"][0]["aggregation_reused"]
    assert {r["engagement"] for r in snapshot_data(report_config)[1]["periods"]["all"]["apps"]} == {
        "unknown"
    }


def test_review_preview_save_and_backup(report_config):
    reports.build_report(report_config)
    service = review.ReviewService(report_config)
    snapshot = service.snapshot()
    before = service.target.read_bytes()
    rules = rules_with("engagement", {"family": "Chrome"}, {"engagement": "other"})
    preview = service.preview(snapshot["revision"], rules)
    assert service.target.read_bytes() == before
    assert "other" in preview["diff"]
    saved = service.save(preview["token"])
    assert saved["saved"] and saved["report_updated"]
    assert Path(saved["backup"]).read_bytes() == before
    assert snapshot_data(report_config)[1]["periods"]["all"]["apps"][0]["engagement"] == "other"
    with pytest.raises(ValueError, match="プレビュー"):
        service.save(preview["token"])


def test_review_stale_revision_and_saved_build_failure(report_config, monkeypatch):
    reports.build_report(report_config)
    service = review.ReviewService(report_config)
    snap = service.snapshot()
    preview = service.preview(snap["revision"], EMPTY_RULES)
    # Any of the four rule files changing invalidates approval, not just this YAML.
    csv_path = Path(report_config["values"]["application_rules_path"])
    csv_path.write_bytes(csv_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="変更"):
        service.save(preview["token"])
    preview = service.preview(
        service.revision(), rules_with("engagement", {"family": "Chrome"}, {"engagement": "other"})
    )

    def fail(*args, **kwargs):
        raise ValueError("fixture failure")

    monkeypatch.setattr(review, "build_report", fail)
    result = service.save(preview["token"])
    assert result["saved"] and not result["report_updated"]
    assert result["error"] == "fixture failure"
    record = json.loads(Path(result["backup"]).with_name("decision.json").read_text("utf-8"))
    assert record["status"] == "saved_report_failed"
    assert (
        parse_application_rules(service.target.read_text("utf-8"))["engagement"][0]["set"][
            "engagement"
        ]
        == "other"
    )


def test_loopback_origin_session_and_path_controls(report_config):
    reports.build_report(report_config)
    with review.make_server(report_config, 0) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            conn.request("GET", "/api/state")
            response = conn.getresponse()
            response.read()
            assert response.status == 404
            conn.request("GET", "/editor", headers={"Host": "evil.example"})
            response = conn.getresponse()
            response.read()
            assert response.status == 403
            conn.request("GET", "/editor")
            response = conn.getresponse()
            response.read()
            cookie = response.getheader("Set-Cookie").split(";")[0]
            assert "HttpOnly" in response.getheader("Set-Cookie")
            headers = {"Cookie": cookie, "Content-Type": "application/json"}
            conn.request("POST", "/api/save", "{}", headers)
            response = conn.getresponse()
            response.read()
            assert response.status == 403
            headers["Origin"] = f"http://127.0.0.1:{server.server_port}"
            conn.request("GET", "/api/state", headers=headers)
            response = conn.getresponse()
            snapshot = json.loads(response.read())
            assert response.status == 200 and snapshot["rules"] == EMPTY_RULES
            conn.request(
                "POST",
                "/api/preview",
                json.dumps({"revision": snapshot["revision"], "rules": EMPTY_RULES}),
                headers,
            )
            response = conn.getresponse()
            assert "token" in json.loads(response.read())
            assert response.status == 200
            conn.request("GET", "/report/%2e%2e/config.yaml", headers=headers)
            response = conn.getresponse()
            response.read()
            assert response.status == 400
            conn.request("GET", "/report/index.html", headers=headers)
            response = conn.getresponse()
            html = response.read()
            assert response.status == 200 and b"engagement-map" in html
            conn.close()
        finally:
            server.shutdown()
            thread.join()
