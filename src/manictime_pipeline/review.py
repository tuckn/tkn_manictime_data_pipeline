"""Loopback-only application rule editor with revision checks and durable backups."""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import mimetypes
import secrets
import threading
import webbrowser
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import unquote, urlsplit

import yaml

from .application_rules import parse_application_rules
from .config import path_value, selected_profile
from .io import child_path, process_lock, read_json, sha256_file
from .report_rules import rule_paths
from .reports import (
    atomic_bytes,
    atomic_json,
    build_report,
    canonical,
    combined_payload,
    root_manifest,
    validate_report_paths,
)

LOG = logging.getLogger(__name__)
MAX_BODY = 1024 * 1024


class ReviewService:
    def __init__(self, config):
        self.config = config
        self.values = config["values"]
        self.root = path_value(self.values["report_path"])
        self.paths = rule_paths(self.values)
        self.target = self.paths["application_overrides_path"]
        self.state = path_value(self.values["state_path"])
        key = hashlib.sha256(str(self.root).casefold().encode()).hexdigest()
        self.report_lock = self.state / "_report-locks" / key
        profiles = [
            selected_profile({**config, "values": {**self.values, "default_profile": name}})
            for name in self.values["profiles"]
        ]
        validate_report_paths(self.root, profiles, list(self.paths.values()))
        self.lock = threading.RLock()
        self.pending = {}

    def revision(self):
        return hashlib.sha256(
            canonical({key: sha256_file(path) for key, path in self.paths.items()})
        ).hexdigest()

    def snapshot(self):
        with self.lock, process_lock(self.report_lock):
            text = self.target.read_text("utf-8-sig")
            data = parse_application_rules(text)
            manifest = root_manifest(self.root)
            payload = combined_payload(self.root, manifest["devices"], self.values["profiles"])
            inventory = [
                {**row, "device_id": pc["device_id"]}
                for pc in payload["profiles"]
                for row in pc["inventory"]
            ]
            return {
                "rules": data,
                "yaml": text,
                "revision": self.revision(),
                "path": str(self.target),
                "inventory": inventory,
            }

    def preview(self, revision, rules):
        text = yaml.safe_dump(rules, allow_unicode=True, sort_keys=False)
        parse_application_rules(text)
        if len(text.encode()) > MAX_BODY:
            raise ValueError("Rules are too large")
        with self.lock, process_lock(self.report_lock):
            if revision != self.revision():
                raise ValueError("設定が変更されています。再読み込みしてから編集してください。")
            old = self.target.read_text("utf-8-sig")
            token = secrets.token_urlsafe(24)
            # Keep only the latest preview. Saving requires this exact reviewed content.
            self.pending = {token: (revision, text)}
            return {
                "token": token,
                "yaml": text,
                "diff": "".join(
                    difflib.unified_diff(
                        old.splitlines(True),
                        text.splitlines(True),
                        fromfile="現在",
                        tofile="保存後",
                    )
                ),
                "counts": {key: len(rules[key]) for key in ("classifications", "engagement")},
            }

    def save(self, token):
        with self.lock:
            if token not in self.pending:
                raise ValueError("プレビューが失効しました。変更を確認し直してください。")
            revision, text = self.pending.pop(token)
            with process_lock(self.report_lock):
                if revision != self.revision():
                    raise ValueError("設定が変更されています。再読み込みしてから編集してください。")
                before = self.target.read_bytes()
                after = text.encode("utf-8")
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4)
                audit = self.state / "rule-edits" / stamp
                record = {
                    "target": str(self.target),
                    "before_sha256": hashlib.sha256(before).hexdigest(),
                    "after_sha256": hashlib.sha256(after).hexdigest(),
                    "status": "prepared",
                }
                atomic_bytes(audit / "before.yaml", before)
                atomic_bytes(audit / "after.yaml", after)
                atomic_json(audit / "decision.json", record)
                atomic_bytes(self.target, after)
            try:
                result = build_report(self.config, all_profiles=True)
            except Exception as exc:
                LOG.exception("Rules saved; report rebuild failed")
                atomic_json(
                    audit / "decision.json",
                    {**record, "status": "saved_report_failed", "error": str(exc)},
                )
                return {
                    "saved": True,
                    "report_updated": False,
                    "error": str(exc),
                    "backup": str(audit / "before.yaml"),
                }
            atomic_json(audit / "decision.json", {**record, "status": "completed"})
            return {
                "saved": True,
                "report_updated": True,
                "index": result["index"],
                "backup": str(audit / "before.yaml"),
            }


def make_server(config, port=8767):
    service = ReviewService(config)
    session = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            LOG.debug(format, *args)

        @property
        def origin(self):
            return f"http://127.0.0.1:{self.server.server_port}"

        def valid_host(self):
            return self.headers.get("Host") == self.origin.removeprefix("http://")

        def authorized(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                return cookie.get("manictime_review") is not None and secrets.compare_digest(
                    cookie["manictime_review"].value, session
                )
            except (ValueError, KeyError):
                return False

        def respond(
            self, data, status=200, content_type="application/json; charset=utf-8", cookie=False
        ):
            if not isinstance(data, bytes):
                data = canonical(data)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            if cookie:
                self.send_header(
                    "Set-Cookie", f"manictime_review={session}; Path=/; HttpOnly; SameSite=Strict"
                )
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.valid_host():
                self.respond({"error": "Local navigation required"}, 403)
                return
            route = unquote(urlsplit(self.path).path)
            try:
                if route == "/editor":
                    self.respond(
                        files("manictime_pipeline").joinpath("resources/review.html").read_bytes(),
                        content_type="text/html; charset=utf-8",
                        cookie=True,
                    )
                elif route == "/api/state" and self.authorized():
                    self.respond(service.snapshot())
                elif route == "/review.js" and self.authorized():
                    self.respond(
                        files("manictime_pipeline").joinpath("resources/review.js").read_bytes(),
                        content_type="text/javascript; charset=utf-8",
                    )
                elif route.startswith("/report/") and self.authorized():
                    relative = route.removeprefix("/report/") or "index.html"
                    target = child_path(service.root, relative)
                    # Only files listed by the current report manifests may be served.
                    manifest = root_manifest(service.root)
                    allowed = {
                        "index.html",
                        "report-manifest.json",
                        manifest.get("history_page", ""),
                    }
                    for entry in manifest["devices"].values():
                        folder = entry["path"]
                        record = read_json(child_path(service.root, folder + "/manifest.json"))
                        allowed.add(folder + "/manifest.json")
                        allowed.update(folder + "/" + name for name in record["outputs"])
                    if relative not in allowed:
                        raise ValueError("Unknown report file")
                    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    self.respond(target.read_bytes(), content_type=mime + "; charset=utf-8")
                else:
                    self.respond({"error": "Not found"}, 404)
            except (ValueError, OSError, KeyError) as exc:
                self.respond({"error": str(exc)}, 400)

        def do_POST(self):
            if (
                not self.valid_host()
                or not self.authorized()
                or self.headers.get("Origin") != self.origin
                or self.headers.get("Content-Type", "").split(";")[0] != "application/json"
            ):
                self.respond({"error": "Local authenticated JSON request required"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if self.path == "/api/preview" and set(body) == {"revision", "rules"}:
                    result = service.preview(body["revision"], body["rules"])
                elif self.path == "/api/save" and set(body) == {"token"}:
                    result = service.save(body["token"])
                else:
                    raise ValueError("Unknown request")
                self.respond(result)
            except (ValueError, OSError, TypeError, KeyError, yaml.YAMLError) as exc:
                self.respond({"error": str(exc)}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = False
    server.review_service = service
    return server


def review(config, port=8767, no_open=False):
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    # Bind before a potentially expensive rebuild, so a port conflict fails immediately.
    with make_server(config, port) as server:
        build_report(config, all_profiles=True)
        url = f"http://127.0.0.1:{server.server_port}/editor"
        LOG.info("Application rule editor: %s (Ctrl+C to stop)", url)
        if not no_open:
            webbrowser.open(url, new=2)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            LOG.info("Application rule editor stopped")
