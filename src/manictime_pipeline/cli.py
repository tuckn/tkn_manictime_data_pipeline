"""Public command interface."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import webbrowser
from pathlib import Path

import yaml

from . import __version__
from .config import config_show, initialize_config, load_config, selected_profile
from .database import inspect_source
from .logging_utils import SUCCESS, configure
from .pipeline import ingest, recover, verify
from .report_rules import initialize_rules
from .reports import build_report


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument(
        "--config", type=Path, help="Additional YAML config (highest file precedence)"
    )
    common.add_argument(
        "--profile",
        help="Select one profile by name; defaults to default_profile (not a file path)",
    )
    verbosity = common.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet", action="store_true", help="Only errors on stderr")
    verbosity.add_argument("-v", "--verbose", action="store_true", help="Include debug diagnostics")
    root = argparse.ArgumentParser(
        prog="tkn-manictime-pipeline",
        parents=[common],
        description="Keep the latest ManicTime DBs, export CSV and build local HTML reports.",
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
    rules = commands.add_parser("rules", parents=[common], help="Create editable report rules")
    rules_commands = rules.add_subparsers(dest="rules_command", required=True)
    rules_init = rules_commands.add_parser(
        "init", parents=[common], help="Create missing rule files; preserve existing files"
    )
    rules_init.add_argument("--dry-run", action="store_true", help="Preview without writing")
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
    report = commands.add_parser(
        "build-report",
        parents=[common],
        help="Update the default PC and build a combined HTML report (writes by default)",
        description="Read successfully ingested CSV and build year/month/week HTML reports. "
        "Updates default_profile; --all updates every profile. Previously built PCs stay visible. "
        "Excludes today; "
        "does not ingest or contact the network. Opens the report after success.",
    )
    report.add_argument(
        "--all",
        action="store_true",
        help="Update all configured profiles; cannot combine with --profile",
    )
    report.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and aggregate without writes or browser launch",
    )
    report.add_argument(
        "--no-open", action="store_true", help="Do not open the browser (for weekly scheduled runs)"
    )
    command = commands.add_parser(
        "recover",
        parents=[common],
        help="Restore an interrupted Raw/CSV update",
        description="Restore an interrupted Raw/CSV update using the state journal. "
        "Writes by default. Use --dry-run for a read-only preview without creating files or state.",
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
            elif args.command == "rules":
                result = initialize_rules(config["values"], args.dry_run)
            elif args.command == "build-report":
                result = build_report(config, args.dry_run, profile_name, args.all)
                for device in result["devices"]:
                    logging.info(
                        "%s: %s, %s days, %s periods, %s dictionary records",
                        device["device_id"],
                        device["action"],
                        device["days"],
                        device["periods"],
                        device["dictionary_records"],
                    )
                logging.info("Report: %s", result["index"])
                if not args.dry_run and not args.no_open:
                    try:
                        if not webbrowser.open(Path(result["index"]).as_uri(), new=2):
                            logging.warning(
                                "Browser could not open the report: %s", result["index"]
                            )
                    except (OSError, webbrowser.Error) as exc:
                        logging.warning("Report created; browser could not open: %s", exc)
            else:
                profile = selected_profile(config)
                if args.command == "inspect":
                    result = inspect_source(profile.source_directory())
                elif args.command == "ingest":
                    result = ingest(profile, args.dry_run)
                elif args.command == "recover":
                    result = recover(profile, args.dry_run)
                else:
                    result = verify(profile)
        logging.log(SUCCESS, "%s completed", args.command)
        if args.command != "build-report":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error, yaml.YAMLError, KeyError, TypeError) as exc:
        logging.error("%s", exc, exc_info=verbose)
        return 1
    except KeyboardInterrupt:
        if args.command == "build-report":
            logging.error("Interrupted; rerun build-report to finish the report")
        else:
            logging.error("Interrupted; run recover before reading Raw or CSV data")
        return 130
