# PYTHON_ARGCOMPLETE_OK
"""Vaudeville CLI entry point.

Usage: uv run python -m vaudeville <command>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import argcomplete
from rich.console import Console

from vaudeville._stats_rendering import print_stats_human
from vaudeville.cli_rules import attach_rule_parsers, dispatch_rule_command
from vaudeville.core.paths import find_project_root as _core_find_project_root
from vaudeville.rules import load_rules_layered

_EVENTS_LOG = str(Path.home() / ".vaudeville" / "logs" / "events.jsonl")

_console = Console()


def cmd_watch(args: argparse.Namespace) -> None:
    """Launch the live watch TUI."""
    import contextlib

    from vaudeville.server import watch

    with contextlib.suppress(KeyboardInterrupt):
        watch(log_path=args.log_path)


def cmd_stats(args: argparse.Namespace) -> None:
    """Print aggregated classification statistics."""
    from vaudeville.server import aggregate_events

    ruleset = load_rules_layered(_find_project_root())
    rule_names = {rule.name for rule in ruleset.rules}
    result = aggregate_events(
        args.log_path,
        allowed_rules=rule_names or None,
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return

    _print_stats_human(result)


def _find_project_root() -> str:
    return _core_find_project_root() or str(Path.cwd())


def _print_stats_human(result: dict[str, Any], console: Console | None = None) -> None:
    print_stats_human(result, console if console is not None else _console)


def _add_log_path_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--log-path",
        default=_EVENTS_LOG,
        help="Path to events.jsonl (default: ~/.vaudeville/logs/events.jsonl)",
    )


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "watch":
        cmd_watch(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif dispatch_rule_command(args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vaudeville",
        description="Vaudeville SLM hook enforcement",
    )
    sub = parser.add_subparsers(dest="command")

    watch_parser = sub.add_parser("watch", help="Live TUI of rule firings")
    _add_log_path_arg(watch_parser)

    stats_parser = sub.add_parser("stats", help="Show classification statistics")
    stats_parser.add_argument("--json", action="store_true", help="Output raw JSON")
    _add_log_path_arg(stats_parser)

    attach_rule_parsers(sub)

    argcomplete.autocomplete(parser)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    _dispatch(args)


if __name__ == "__main__":
    main()
