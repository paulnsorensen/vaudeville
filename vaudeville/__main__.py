"""Vaudeville CLI entry point.

Usage: uv run python -m vaudeville <command>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from rich.console import Console

from vaudeville import orchestrator
from vaudeville._stats_rendering import print_stats_human
from vaudeville.cli_rules import attach_rule_parsers, dispatch_rule_command
from vaudeville.core.paths import find_project_root as _core_find_project_root
from vaudeville.orchestrator import RalphError, Thresholds


_EVENTS_LOG = os.path.join(
    os.path.expanduser("~"), ".vaudeville", "logs", "events.jsonl"
)

_console = Console()


def cmd_watch(args: argparse.Namespace) -> None:
    """Launch the live watch TUI."""
    from vaudeville.server import watch

    try:
        watch(log_path=args.log_path)
    except KeyboardInterrupt:
        pass


def cmd_setup(_args: argparse.Namespace) -> None:
    """Run model download and verification."""
    from vaudeville.setup import main as setup_main

    setup_main()


def cmd_stats(args: argparse.Namespace) -> None:
    """Print aggregated classification statistics."""
    from vaudeville.server import aggregate_events

    result = aggregate_events(args.log_path)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    _print_stats_human(result)


def _find_project_root() -> str:
    return _core_find_project_root() or os.getcwd()


def _strict_project_root() -> str | None:
    return _core_find_project_root()


def _resolve_rules_dir(scope: str, strict_root: str | None) -> str:
    if scope == "global":
        return os.path.join(os.path.expanduser("~"), ".vaudeville", "rules")
    if strict_root is None:
        print("error: --scope project requires a git project root", file=sys.stderr)
        sys.exit(2)
    return os.path.join(strict_root, ".vaudeville", "rules")


def _find_commands_dir() -> str:
    """Return commands/ root, honoring VAUDEVILLE_COMMANDS_DIR if set."""
    override = os.environ.get("VAUDEVILLE_COMMANDS_DIR")
    if override:
        return override
    return os.path.join(_find_project_root(), "commands")


def _threshold_float(value: str) -> float:
    v = float(value)
    if not (0.0 <= v <= 1.0):
        raise argparse.ArgumentTypeError(f"must be between 0.0 and 1.0, got {v}")
    return v


def cmd_tune(args: argparse.Namespace) -> int:
    """Tune a rule via the multi-phase design→tune→judge pipeline."""
    strict_root = _strict_project_root()
    project_root = strict_root or os.getcwd()
    commands_dir = _find_commands_dir()
    rules_dir = _resolve_rules_dir(args.scope, strict_root)
    thresholds = Thresholds(p_min=args.p_min, r_min=args.r_min, f1_min=args.f1_min)
    try:
        return orchestrator.orchestrate_tune(
            rule_name=args.rule,
            thresholds=thresholds,
            rounds=args.rounds,
            tuner_iters=args.tuner_iters,
            project_root=project_root,
            commands_dir=commands_dir,
            rules_dir=rules_dir,
        )
    except RalphError as e:
        print(str(e), file=sys.stderr)
        return 2


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate new rules via the multi-phase design→tune→judge pipeline."""
    strict_root = _strict_project_root()
    project_root = strict_root or os.getcwd()
    commands_dir = _find_commands_dir()
    rules_dir = _resolve_rules_dir(args.scope, strict_root)
    mode = "live" if args.live else "shadow"
    thresholds = Thresholds(p_min=args.p_min, r_min=args.r_min, f1_min=args.f1_min)
    try:
        return orchestrator.orchestrate_generate(
            instructions=args.instructions,
            thresholds=thresholds,
            rounds=args.rounds,
            tuner_iters=args.tuner_iters,
            mode=mode,
            project_root=project_root,
            commands_dir=commands_dir,
            rules_dir=rules_dir,
        )
    except RalphError as e:
        print(str(e), file=sys.stderr)
        return 2


def _print_stats_human(result: dict[str, Any], console: Console | None = None) -> None:
    print_stats_human(result, console if console is not None else _console)


def _add_log_path_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--log-path",
        default=_EVENTS_LOG,
        help="Path to events.jsonl (default: ~/.vaudeville/logs/events.jsonl)",
    )


def _add_pipeline_args(p: argparse.ArgumentParser) -> None:
    """Add threshold + orchestration args shared by tune and generate."""
    p.add_argument(
        "--p-min",
        type=_threshold_float,
        default=0.95,
        help="Minimum precision threshold (default: 0.95)",
    )
    p.add_argument(
        "--r-min",
        type=_threshold_float,
        default=0.80,
        help="Minimum recall threshold (default: 0.80)",
    )
    p.add_argument(
        "--f1-min",
        type=_threshold_float,
        default=0.85,
        help="Minimum F1 threshold (default: 0.85)",
    )
    p.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="Maximum orchestration rounds (default: 3)",
    )
    p.add_argument(
        "--tuner-iters",
        type=int,
        default=10,
        help="Ralph iterations for tune phase (default: 10)",
    )
    p.add_argument(
        "--scope",
        choices=("project", "global"),
        default="global",
        help="Where the rule lives: project (.vaudeville/rules/) or "
        "global (~/.vaudeville/rules/) [default: global]",
    )


def _build_tune_parser(sub: Any) -> None:
    p = sub.add_parser(
        "tune",
        help="Tune a rule to meet precision/recall/f1 thresholds (autonomous agent)",
    )
    p.add_argument("rule", help="Rule name to tune")
    _add_pipeline_args(p)


def _build_generate_parser(sub: Any) -> None:
    p = sub.add_parser(
        "generate",
        help="Generate a new rule from instructions (autonomous agent)",
    )
    p.add_argument(
        "instructions",
        help="Description of what the rule should detect",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="Run generation in live mode instead of shadow mode",
    )
    _add_pipeline_args(p)


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "watch":
        cmd_watch(args)
    elif args.command == "setup":
        cmd_setup(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "tune":
        sys.exit(cmd_tune(args))
    elif args.command == "generate":
        sys.exit(cmd_generate(args))
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

    sub.add_parser("setup", help="Download model and verify inference")

    stats_parser = sub.add_parser("stats", help="Show classification statistics")
    stats_parser.add_argument("--json", action="store_true", help="Output raw JSON")
    _add_log_path_arg(stats_parser)

    _build_tune_parser(sub)
    _build_generate_parser(sub)
    attach_rule_parsers(sub)

    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    _dispatch(args)


if __name__ == "__main__":
    main()
