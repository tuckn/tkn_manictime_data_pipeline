"""Strict YAML configuration, provenance, and application paths."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import yaml

SCHEMA_VERSION = "2.1.0"
PROFILE_KEYS = {"device_id", "source_path", "raw_path", "processed_data_path"}
REQUIRED_PROFILE_KEYS = {"device_id", "source_path"}
OUTPUT_KEYS = ("raw_path", "processed_data_path")
TOP_KEYS = {
    "schema_version",
    "default_profile",
    "raw_path",
    "processed_data_path",
    "state_path",
    "backup_timeout_seconds",
    "max_activity_drop_percent",
    "profiles",
}


def app_root() -> Path:
    return Path.home() / ".tkn" / "manictime_data_pipeline"


def path_value(value: str) -> Path:
    return Path(value).expanduser().resolve()


def safe_component(value: str) -> str:
    if (
        not value.strip()
        or value in {".", ".."}
        or value.endswith((".", " "))
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
        or value.split(".")[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
    ):
        raise ValueError(f"Invalid directory component: {value!r}")
    return value


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("YAML mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _validate(data: object, source: Path) -> dict:
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {source}")
    unknown = set(data) - TOP_KEYS
    if unknown:
        raise ValueError(f"Unknown config keys in {source}: {sorted(unknown)}")
    version = data.get("schema_version")
    if not isinstance(version, str) or not re.fullmatch(r"2\.[01]\.\d+", version):
        raise ValueError(
            f"Unsupported schema_version {version!r} in {source}; supported: 2.0.x and 2.1.x. "
            "For 1.0.x, follow the README upgrade instructions: raw_path now names "
            "a parent directory shared by devices. Back up and update the config explicitly."
        )
    for key, value in data.items():
        if key in {"schema_version", "profiles"}:
            continue
        if key == "backup_timeout_seconds":
            if type(value) is not int or not 1 <= value <= 86400:
                raise ValueError("backup_timeout_seconds must be an integer from 1 to 86400")
        elif key == "max_activity_drop_percent":
            if type(value) not in {int, float} or not 0 <= value <= 100:
                raise ValueError("max_activity_drop_percent must be a number from 0 to 100")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string in {source}")
    if "profiles" in data:
        if not isinstance(data["profiles"], dict):
            raise ValueError("profiles must be a mapping keyed by profile name")
        for name, profile in data["profiles"].items():
            safe_component(name)
            if not isinstance(profile, dict) or set(profile) - PROFILE_KEYS:
                raise ValueError(f"Invalid or unknown profile keys: {name}")
            for key, value in profile.items():
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"profiles.{name}.{key} must be a non-empty string")
                if key == "device_id":
                    safe_component(value)
    return data


@dataclass(frozen=True)
class Profile:
    name: str
    device_id: str
    source_path: Path
    raw_path: Path
    processed_path: Path
    state_path: Path
    backup_timeout_seconds: int
    max_activity_drop_percent: float = 10.0

    @property
    def raw_directory(self) -> Path:
        """Per-device capture directory; raw_path is always its parent."""
        return self.raw_path / self.device_id

    def source_directory(self) -> Path:
        candidates = [
            p
            for p in (self.source_path, self.source_path / "Data")
            if (p / "ManicTimeReports.db").is_file()
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"Expected exactly one DB directory at {self.source_path} or its Data child; "
                "both ManicTimeCore.db and ManicTimeReports.db are required"
            )
        directory = candidates[0]
        for name in ("ManicTimeCore.db", "ManicTimeReports.db"):
            if not (directory / name).is_file():
                raise ValueError(f"Missing source database: {directory / name}")
        return directory

    def validate_paths(self) -> None:
        roots = [
            p.resolve()
            for p in (self.source_path, self.raw_directory, self.processed_path, self.state_path)
        ]
        for i, first in enumerate(roots):
            for second in roots[i + 1 :]:
                if first == second or first in second.parents or second in first.parents:
                    raise ValueError(f"Source/output/state paths overlap: {first} and {second}")


def load_config(explicit: Path | None = None, profile_name: str | None = None) -> dict:
    values = {
        "backup_timeout_seconds": 300,
        "max_activity_drop_percent": 10.0,
        "raw_path": str(app_root() / "data" / "raw"),
        "processed_data_path": str(app_root() / "data" / "csv"),
        "state_path": str(app_root() / "state"),
        "profiles": {},
    }
    origins = {key: "built-in" for key in values if key != "profiles"}
    sources = []
    paths = [app_root() / "config.yaml", Path.cwd() / ".tkn" / "config.yaml"]
    if explicit is not None:
        explicit = explicit.expanduser().resolve()
        if not explicit.is_file():
            raise ValueError(f"Configuration file does not exist: {explicit}")
        paths.append(explicit)
    seen = set()
    for path in paths:
        path = path.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        data = _validate(yaml.load(path.read_text(encoding="utf-8-sig"), UniqueLoader), path)
        sources.append({"path": str(path), "schema_version": data["schema_version"]})
        for key, value in data.items():
            if key == "schema_version":
                continue
            if key == "profiles":
                for name, props in value.items():
                    values["profiles"].setdefault(name, {}).update(props)
                    for prop in props:
                        origins[f"profiles.{name}.{prop}"] = str(path)
            else:
                values[key] = value
                origins[key] = str(path)
    if profile_name is not None:
        values["default_profile"] = profile_name
        origins["default_profile"] = "CLI --profile"
    return {
        "schema_version": SCHEMA_VERSION,
        "values": values,
        "sources": sources,
        "winning_sources": origins,
    }


def selected_profile(config: dict) -> Profile:
    data = config["values"]
    name = data.get("default_profile")
    profiles = data["profiles"]
    if name not in profiles:
        origin = config["winning_sources"].get("default_profile", "not configured")
        available = ", ".join(sorted(profiles)) or "(none)"
        sources = ", ".join(source["path"] for source in config["sources"]) or "(none)"
        if name is None:
            problem = "default_profile is not configured."
        else:
            problem = f"Selected profile {name!r} does not exist (selected by {origin})."
        if profiles:
            remedy = "Set default_profile to an available profile name or use --profile NAME."
        else:
            remedy = (
                "Run config init and configure profiles and default_profile in "
                f"{app_root() / 'config.yaml'}, or supply --config PATH."
            )
        raise ValueError(
            f"{problem} Available profiles: {available}. Loaded config files: {sources}. "
            f"{remedy} Use config show to inspect resolved settings."
        )
    p = profiles[name]
    missing = REQUIRED_PROFILE_KEYS - set(p)
    if missing:
        raise ValueError(f"Configure profiles.{name}: missing {sorted(missing)}")
    # Prevent different profiles silently sharing the same publication directory.
    devices: dict[str, list[str]] = {}
    for profile_name, values in profiles.items():
        if values.get("device_id"):
            devices.setdefault(values["device_id"].casefold(), []).append(profile_name)
    conflicts = []
    for names in devices.values():
        if len(names) > 1:
            entries = [
                f"{n!r} ({config['winning_sources'].get(f'profiles.{n}.device_id', 'unknown')})"
                for n in names
            ]
            conflicts.append(f"{profiles[names[0]]['device_id']!r}: " + ", ".join(entries))
    if conflicts:
        raise ValueError(
            "device_id must be unique across profiles (case-insensitive). Conflicts: "
            + "; ".join(conflicts)
            + ". Profiles from config files merge by profile name. "
            "Use config show to inspect resolved settings."
        )
    result = Profile(
        name,
        p["device_id"],
        path_value(p["source_path"]),
        path_value(p.get("raw_path", data["raw_path"])),
        path_value(p.get("processed_data_path", data["processed_data_path"])) / p["device_id"],
        path_value(data["state_path"]) / name,
        data["backup_timeout_seconds"],
        data["max_activity_drop_percent"],
    )
    result.validate_paths()
    return result


def config_show(config: dict) -> dict:
    result = dict(config)
    values = dict(config["values"])
    for key in ("state_path", *OUTPUT_KEYS):
        if key in values:
            values[key] = str(path_value(values[key]))
    values["profiles"] = {
        name: {
            key: str(path_value(value)) if key.endswith("_path") else value
            for key, value in profile.items()
        }
        for name, profile in values["profiles"].items()
    }
    result["values"] = values
    effective = {}
    for name, profile in values["profiles"].items():
        entry = {key: profile.get(key, values[key]) for key in OUTPUT_KEYS}
        entry["winning_sources"] = {
            key: config["winning_sources"].get(
                f"profiles.{name}.{key}", config["winning_sources"][key]
            )
            for key in OUTPUT_KEYS
        }
        if "device_id" in profile:
            entry["raw_directory"] = str(Path(entry["raw_path"]) / profile["device_id"])
            entry["csv_directory"] = str(Path(entry["processed_data_path"]) / profile["device_id"])
        effective[name] = entry
    result["effective_profiles"] = effective
    result["user_config_path"] = str(app_root() / "config.yaml")
    return result


def initialize_config(dry_run: bool = False) -> dict:
    target = app_root() / "config.yaml"
    payload = files("manictime_pipeline").joinpath("resources/config.example.yaml").read_bytes()
    if target.exists():
        if target.read_bytes() != payload:
            raise ValueError(
                f"Edited configuration is preserved; open it to make changes: {target}"
            )
        return {"action": "unchanged", "path": str(target)}
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    return {"action": "would_create" if dry_run else "created", "path": str(target)}
