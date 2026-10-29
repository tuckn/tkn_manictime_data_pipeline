"""Public command interface."""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path
from typing import NoReturn

from . import __version__
from .config import (
    app_root,
    config_list,
    initialize_config,
    load_config,
    path_value,
    selected_profile,
)
from .database import inspect_source
from .logging_utils import SUCCESS, configure
from .output import config_lines as config_lines
from .output import emit, failure_result, public_result
from .pipeline import ingest, recover, verify
from .report_rules import initialize_rules
from .reports import build_report


class ArgumentError(ValueError):
    """Argument usage error, retaining exit code 2 while allowing selected output."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ArgumentError(message)


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
    common.add_argument(
        "--json",
        action="store_true",
        help="Print one machine-readable JSON result instead of the default key=value list",
    )
    common.add_argument(
        "--full-output",
        action="store_true",
        help="Include unchanged partition details, in either text or JSON results",
    )
    root = Parser(
        prog="tkn-manictime-pipeline",
        parents=[common],
        description="Keep the latest ManicTime DBs, export CSV and build local HTML reports.",
        epilog="Results: key=value by default; --json selects JSON. Progress and diagnostics "
        "go to stderr. --quiet/--verbose affect logs only. Exit codes: 0 success, "
        "1 configuration/processing failure, 2 argument error, 130 interrupted.",
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
        "list",
        parents=[common],
        help="List resolved values and winning sources",
        description="Read settings without writing files. Print one key=value per line; "
        "nested keys use dots and list items use indexes. Windows paths are copyable.",
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
    editor = commands.add_parser(
        "review",
        parents=[common],
        help="Edit application rules in a local browser; rebuild all PC reports",
    )
    editor.add_argument(
        "--port",
        type=int,
        default=8767,
        help="Loopback port (default 8767; 0 chooses an available port)",
    )
    editor.add_argument(
        "--no-open", action="store_true", help="Do not open the browser automatically"
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
    argv = list(sys.argv[1:] if argv is None else argv)
    option_tokens = argv[: argv.index("--")] if "--" in argv else argv
    json_output = "--json" in option_tokens
    verbose = "--verbose" in option_tokens or "-v" in option_tokens
    configure("--quiet" in option_tokens or "-q" in option_tokens, verbose)
    command = None
    args = None
    targets = []
    try:
        args = parser().parse_args(argv)
        command = " ".join(
            filter(
                None,
                [
                    args.command,
                    getattr(args, "config_command", None),
                    getattr(args, "rules_command", None),
                ],
            )
        )
        json_output = getattr(args, "json", False)
        verbose = getattr(args, "verbose", False)
        quiet = getattr(args, "quiet", False)
        if verbose and quiet:
            raise ArgumentError("--quiet and --verbose cannot be combined")
        configure(quiet, verbose)
        explicit = getattr(args, "config", None)
        profile_name = getattr(args, "profile", None)
        targets = (
            [{"role": "config", "path": str(path_value(str(explicit)))}]
            if explicit
            else [
                {"role": "user_config", "path": str(app_root() / "config.yaml")},
                {"role": "project_config", "path": str(Path.cwd() / ".tkn/config.yaml")},
            ]
        )
        if args.command == "config" and args.config_command == "init":
            targets = [{"role": "user_config", "path": str(app_root() / "config.yaml")}]
            if explicit is not None or profile_name is not None:
                raise ValueError("config init targets the user config; omit --config and --profile")
            result = initialize_config(args.dry_run)
        else:
            config = load_config(explicit, profile_name)
            if args.command in {"rules", "build-report", "review"}:
                from .report_rules import rule_paths

                targets = [
                    {"role": key, "path": str(path)}
                    for key, path in rule_paths(config["values"]).items()
                ]
                targets.append(
                    {
                        "role": "report_path",
                        "path": str(path_value(config["values"]["report_path"])),
                    }
                )
            if args.command == "config":
                result = config_list(config)
            elif args.command == "rules":
                result = initialize_rules(config["values"], args.dry_run)
            elif args.command == "review":
                from .review import review

                if profile_name:
                    raise ValueError("review updates all profiles; omit --profile")
                review(config, args.port, args.no_open)
                root = path_value(config["values"]["report_path"])
                result = {
                    "action": "stopped",
                    "report_path": str(root),
                    "index": str(root / "index.html"),
                }
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
                targets = [
                    {"role": role, "path": str(path)}
                    for role, path in (
                        ("source_path", profile.source_path),
                        ("raw_directory", profile.raw_directory),
                        ("processed_path", profile.processed_path),
                        ("state_path", profile.state_path),
                    )
                ]
                if args.command == "inspect":
                    result = inspect_source(profile.source_directory())
                elif args.command == "ingest":
                    result = ingest(profile, args.dry_run)
                elif args.command == "recover":
                    result = recover(profile, args.dry_run)
                else:
                    result = verify(profile)
        listing = args.command == "config" and args.config_command == "list"
        if listing:
            logging.info("Showing resolved configuration")
        else:
            logging.log(SUCCESS, "%s completed", args.command)
        emit(
            public_result(command, result, full_output=getattr(args, "full_output", False)),
            json_output=json_output,
        )
        return 0
    except Exception as exc:
        logging.error("%s", exc, exc_info=verbose)
        code = 2 if isinstance(exc, ArgumentError) else 1
        emit(failure_result(command, exc, code, targets), json_output=json_output)
        return code
    except KeyboardInterrupt as exc:
        if getattr(args, "command", None) == "build-report":
            remedy = "Interrupted; rerun build-report to finish the report"
        else:
            remedy = "Interrupted; run recover before reading Raw or CSV data"
        logging.error("%s", remedy)
        result = failure_result(command, exc, 130, targets)
        result["recovery_hint"] = remedy
        emit(result, json_output=json_output)
        return 130
