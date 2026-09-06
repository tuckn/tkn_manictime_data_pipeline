"""Public command interface."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path

import yaml

from . import __version__
from .config import config_show, initialize_config, load_config, selected_profile
from .database import inspect_source
from .logging_utils import SUCCESS, configure
from .pipeline import ingest, migrate_layout, recover, verify


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument(
        "--config", type=Path, help="Additional YAML config (highest file precedence)"
    )
    common.add_argument(
        "--profile",
        help="Profile name in merged config; defaults to default_profile (not a file path)",
    )
    verbosity = common.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet", action="store_true", help="Only errors on stderr")
    verbosity.add_argument("-v", "--verbose", action="store_true", help="Include debug diagnostics")
    root = argparse.ArgumentParser(
        prog="tkn-manictime-pipeline",
        parents=[common],
        description="Keep the latest ManicTime DBs and export changed CSV partitions. Local-only.",
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config", parents=[common], help="Create or inspect YAML settings")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    init = config_commands.add_parser(
        "init", parents=[common], help="Safely create user configuration"
    )
    init.add_argument("--dry-run", action="store_true", help="Preview; do not create a config file")
    config_commands.add_parser(
        "show", parents=[common], help="Show resolved values and winning sources"
    )
    commands.add_parser("inspect", parents=[common], help="Read DB schema, counts and date range")
    run = commands.add_parser(
        "ingest",
        parents=[common],
        help="Replace latest Raw and publish changed CSV partitions (writes by default)",
        description="Capture and validate both DBs, then replace latest Raw and changed "
        "BOM-free UTF-8/LF CSV partitions. Keep one Raw generation after success; "
        "stop if activity reduction exceeds max_activity_drop_percent. "
        "No source deletion, networking, AI, external sqlite3 or browser launch.",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare DB/state read-only; no backup, quick_check, file writes, "
        "cache, persistent temp, networking or AI. Scans all selected source tables.",
    )
    commands.add_parser(
        "verify", parents=[common], help="Verify published CSV against its Raw capture"
    )
    for name, help_text in [
        ("recover", "Restore an interrupted Raw/CSV update using the state journal"),
        (
            "migrate-layout",
            "Migrate v0.1-v0.3 to latest Raw and fixed CSV paths; preserve legacy files",
        ),
    ]:
        command = commands.add_parser(
            name,
            parents=[common],
            help=help_text,
            description=f"{help_text}. Writes by default. "
            "Use --dry-run for a read-only preview without creating files or state.",
        )
        command.add_argument("--dry-run", action="store_true", help="Read-only preview; no writes")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    verbose = getattr(args, "verbose", False)
    quiet = getattr(args, "quiet", False)
    if verbose and quiet:
        parser().error("--quiet and --verbose cannot be combined")
    configure(quiet, verbose)
    try:
        explicit = getattr(args, "config", None)
        profile_name = getattr(args, "profile", None)
        if args.command == "config" and args.config_command == "init":
            if explicit is not None or profile_name is not None:
                raise ValueError("config init targets the user config; omit --config and --profile")
            result = initialize_config(args.dry_run)
        else:
            config = load_config(explicit, profile_name)
            if args.command == "config":
                result = config_show(config)
            else:
                profile = selected_profile(config)
                if args.command == "inspect":
                    result = inspect_source(profile.source_directory())
                elif args.command == "ingest":
                    result = ingest(profile, args.dry_run)
                elif args.command == "recover":
                    result = recover(profile, args.dry_run)
                elif args.command == "migrate-layout":
                    result = migrate_layout(profile, args.dry_run)
                else:
                    result = verify(profile)
        logging.log(SUCCESS, "%s completed", args.command)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error, yaml.YAMLError, KeyError, TypeError) as exc:
        logging.error("%s", exc, exc_info=verbose)
        return 1
    except KeyboardInterrupt:
        logging.error("Interrupted; run recover before reading Raw or CSV data")
        return 130
