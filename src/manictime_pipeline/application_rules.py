"""Editable application hierarchy and user-defined activity labels."""

from __future__ import annotations

import fnmatch
import json
from datetime import date

import yaml

from .config import UniqueLoader

ROLES = ("focus", "distracted", "other", "unknown")
FIELDS = ("category", "family", "channel", "purpose")
CATEGORIES = {"Browser", "Desktop app", "System / Shell", "Unknown"}
MATCH_FIELDS = {
    "file_name",
    "key",
    "name",
    "title",
    "family",
    "channel",
    "device_id",
    "group_path_prefix",
}
EMPTY_RULES = {"schema_version": "1.0.0", "classifications": [], "engagement": []}


def parse_application_rules(text):
    data = yaml.load(text, UniqueLoader)
    if (
        not isinstance(data, dict)
        or set(data) != set(EMPTY_RULES)
        or data["schema_version"] != "1.0.0"
    ):
        raise ValueError(
            "Application rules require schema_version: 1.0.0, classifications and engagement"
        )
    ids = set()
    for section in ("classifications", "engagement"):
        if not isinstance(data[section], list):
            raise ValueError(f"{section} must be a list")
        for rule in data[section]:
            required = {"id", "match", "set"}
            if (
                not isinstance(rule, dict)
                or not required <= set(rule)
                or set(rule) - required - {"valid_from", "valid_to"}
            ):
                raise ValueError("Rule requires id, match and set; optional valid_from / valid_to")
            if not isinstance(rule["id"], str) or not rule["id"].strip() or rule["id"] in ids:
                raise ValueError("Rule IDs must be nonempty and unique")
            ids.add(rule["id"])
            match, patch = rule["match"], rule["set"]
            if not isinstance(match, dict) or not match or set(match) - MATCH_FIELDS:
                raise ValueError(f"Invalid match fields in {rule['id']}")
            if any(not isinstance(v, str) or not v.strip() for v in match.values()):
                raise ValueError("Match values must be nonempty strings")
            if "group_path_prefix" in match and any(
                c in match["group_path_prefix"] for c in "*?[]"
            ):
                raise ValueError("group_path_prefix must be a literal folder, without wildcards")
            allowed = set(FIELDS) if section == "classifications" else {"engagement"}
            if not isinstance(patch, dict) or not patch or set(patch) - allowed:
                raise ValueError(f"Invalid set fields in {rule['id']}")
            if any(not isinstance(v, str) or not v.strip() for v in patch.values()):
                raise ValueError("Set values must be nonempty strings")
            if "category" in patch and patch["category"] not in CATEGORIES:
                raise ValueError("Invalid category")
            if "engagement" in patch and patch["engagement"] not in ROLES:
                raise ValueError("Invalid engagement; use focus, distracted, other or unknown")
            for key in ("valid_from", "valid_to"):
                if key in rule:
                    if (
                        not isinstance(rule[key], str)
                        or date.fromisoformat(rule[key]).isoformat() != rule[key]
                    ):
                        raise ValueError("Rule dates must be quoted YYYY-MM-DD strings")
            if rule.get("valid_from", "") > rule.get("valid_to", "9999-12-31"):
                raise ValueError("Rule dates are reversed")
    return data


def group_context(group, title, device):
    meta = json.loads(group.get("Other") or "{}")
    key = group.get("Key") or ""
    return {
        "key": key,
        "name": group.get("Name") or "Unknown",
        "title": title,
        "file_name": meta.get("fileName") or key.split(";")[0],
        "group_path_prefix": meta.get("fullPath") or "",
        "device_id": device,
    }


def matches(rule, context, day):
    if day < rule.get("valid_from", "") or day > rule.get("valid_to", "9999-12-31"):
        return False
    for key, pattern in rule["match"].items():
        value = context.get(key, "").casefold()
        if key == "group_path_prefix":
            prefix = pattern.replace("\\", "/").rstrip("/").casefold() + "/"
            if not value.replace("\\", "/").startswith(prefix):
                return False
        elif not fnmatch.fnmatchcase(value, pattern.casefold()):
            return False
    return True


def classify_application(base, group, title, device, day, rules=None):
    rules = rules or EMPTY_RULES
    result = dict(zip(FIELDS, base, strict=True))
    context = {**group_context(group, title.replace("\u200b", ""), device), **result}
    evidence = {"classification_rule": "", "engagement_rule": ""}
    for rule in rules["classifications"]:
        if matches(rule, context, day):
            result.update(rule["set"])
            evidence["classification_rule"] = rule["id"]
            break
    context.update(result)
    role = "unknown"
    for rule in rules["engagement"]:
        if matches(rule, context, day):
            role = rule["set"]["engagement"]
            evidence["engagement_rule"] = rule["id"]
            break
    return (*[result[k] for k in FIELDS], role), evidence
