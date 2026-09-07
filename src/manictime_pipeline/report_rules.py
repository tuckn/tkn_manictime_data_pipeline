"""User-owned report rules and validated, declarative URL/title extraction."""

from __future__ import annotations

import re
import unicodedata
from importlib.resources import files
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

from .config import UniqueLoader, app_root, path_value

RULE_FILES = {
    "application_rules_path": "application_rules.csv",
    "site_rules_path": "site_rules.csv",
    "extraction_rules_path": "extraction_rules.yaml",
}


def rule_paths(values):
    return {
        key: path_value(values.get(key, str(app_root() / "rules" / name)))
        for key, name in RULE_FILES.items()
    }


def initialize_rules(values, dry_run=False):
    """Create missing files exclusively; never replace user edits."""
    result = []
    for key, target in rule_paths(values).items():
        name = RULE_FILES[key]
        resource = name.replace(".csv", ".example.csv")
        payload = files("manictime_pipeline").joinpath("resources", resource).read_bytes()
        exists = target.exists()
        if not exists and not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(payload)
        result.append(
            {
                "path": str(target),
                "action": "preserved" if exists else "would_create" if dry_run else "created",
            }
        )
    return result


def extraction_rules(path):
    if not path.is_file():
        raise ValueError(f"Missing report rules: {path}; run rules init first")
    return parse_extraction_rules(path.read_text("utf-8-sig"))


def parse_extraction_rules(text):
    data = yaml.load(text, UniqueLoader)
    if not isinstance(data, dict) or set(data) != {"schema_version", "rules"}:
        raise ValueError("Extraction rules require schema_version and rules")
    if data["schema_version"] != "1.0.0" or not isinstance(data["rules"], list):
        raise ValueError("Unsupported extraction rules schema")
    ids = set()
    for rule in data["rules"]:
        required = {"id", "kind", "hosts", "path_pattern"}
        allowed = required | {"title_pattern", "query_parameter", "strip_suffix"}
        if not isinstance(rule, dict) or not required <= set(rule) or set(rule) - allowed:
            raise ValueError("Invalid extraction rule fields")
        if rule["kind"] not in {"dictionary", "search"}:
            raise ValueError("Extraction kind must be dictionary or search")
        if not isinstance(rule["id"], str) or not rule["id"] or rule["id"] in ids:
            raise ValueError("Extraction rule IDs must be nonempty and unique")
        ids.add(rule["id"])
        if (
            not isinstance(rule["hosts"], list)
            or not rule["hosts"]
            or any(
                not isinstance(h, str) or not re.fullmatch(r"[a-z0-9.-]+", h) for h in rule["hosts"]
            )
        ):
            raise ValueError("Extraction hosts must be exact lowercase host names")
        for field in ("path_pattern", "title_pattern", "strip_suffix"):
            if field in rule:
                if not isinstance(rule[field], str) or not rule[field]:
                    raise ValueError(f"{field} must be a nonempty regex")
                try:
                    pattern = re.compile(rule[field], re.I)
                except re.error as exc:
                    raise ValueError(f"Invalid {field} in {rule['id']}: {exc}") from exc
                if field != "strip_suffix" and rule["kind"] == "dictionary" and pattern.groups < 1:
                    raise ValueError("Dictionary patterns require a capture group for the term")
        if "query_parameter" in rule and (
            not isinstance(rule["query_parameter"], str) or not rule["query_parameter"]
        ):
            raise ValueError("query_parameter must be a nonempty string")
        if rule["kind"] == "search" and "query_parameter" not in rule:
            raise ValueError("Search rules require query_parameter")
    return data["rules"]


def extract(url, title, rules, kind):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".").removeprefix("www.")
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    for rule in rules:
        if rule["kind"] != kind or host not in rule["hosts"]:
            continue
        match = re.search(rule["path_pattern"], unquote(parsed.path), re.I)
        query = parse_qs(parsed.query).get(rule.get("query_parameter", "q"), [""])[0]
        if kind == "search":
            if match and query.strip():
                return {
                    "host": host,
                    "term": query.strip(),
                    "search_term": query,
                    "method": "query",
                    "rule_id": rule["id"],
                }
            continue
        method = "url"
        if not match and rule.get("title_pattern"):
            match = re.search(rule["title_pattern"], title, re.I)
            method = "title"
        if not match:
            continue
        term = match[1]
        if rule.get("strip_suffix"):
            term = re.sub(rule["strip_suffix"], "", term)
        term = unicodedata.normalize("NFKC", term).strip().strip('「」"').casefold()
        if term and len(term) <= 120:
            return {
                "host": host,
                "term": term,
                "search_term": query or term,
                "method": method,
                "rule_id": rule["id"],
            }
    return None
