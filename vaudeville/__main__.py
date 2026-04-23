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
from rich.text import Text

from vaudeville import orchestrator
from vaudeville.cli_rules import attach_rule_parsers, dispatch_rule_command
from vaudeville.core.paths import find_project_root as _core_find_project_root
from vaudeville.orchestrator import RalphError, Thresholds
from vaudeville.server import latency_text, styled_table


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
    project_root = _find_project_root()
    commands_dir = _find_commands_dir()
    thresholds = Thresholds(p_min=args.p_min, r_min=args.r_min, f1_min=args.f1_min)
    try:
        return orchestrator.orchestrate_tune(
            rule_name=args.rule,
            thresholds=thresholds,
            rounds=args.rounds,
            tuner_iters=args.tuner_iters,
            project_root=project_root,
            commands_dir=commands_dir,
        )
    except RalphError as e:
        print(str(e), file=sys.stderr)
        return 2


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate new rules via the multi-phase design→tune→judge pipeline."""
    project_root = _find_project_root()
    commands_dir = _find_commands_dir()
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
        )
    except RalphError as e:
        print(str(e), file=sys.stderr)
        return 2


def _render_rules_table(con: Console, rules: dict[str, Any], total: int) -> None:
    table = styled_table(
        "Per-Rule Breakdown",
        caption=f"{total} classifications",
    )
    table.add_column("Rule", justify="left")
    for col in ("Total", "Violations", "Pass %", "Avg ms", "p50 ms", "p95 ms"):
        table.add_column(col, justify="right")

    for name, data in rules.items():
        pass_rate = data["pass_rate"]
        style = "green" if pass_rate >= 90 else "yellow" if pass_rate >= 50 else "red"
        table.add_row(
            name,
            str(data["total"]),
            str(data["violations"]),
            Text(f"{pass_rate:.1f}%", style=style),
            latency_text(data["avg_latency_ms"]),
            latency_text(data["p50_latency_ms"]),
            latency_text(data["p95_latency_ms"]),
        )
    con.print(table)


def _render_latency_summary(con: Console, lat: dict[str, Any]) -> None:
    summary = Text.assemble(
        "Overall latency \u2014 p50: ",
        (f"{lat['p50_ms']:.1f}ms", "bold"),
        "   p95: ",
        (f"{lat['p95_ms']:.1f}ms", "bold"),
        "   mean: ",
        (f"{lat['mean_ms']:.1f}ms", "bold"),
    )
    con.print(summary)
    con.print()


def _render_histogram(con: Console, histogram: dict[str, int]) -> None:
    hist_table = styled_table("Latency Histogram")
    hist_table.add_column("Bucket", justify="right")
    hist_table.add_column("Count", justify="right")
    hist_table.add_column("Bar")

    max_count = max(histogram.values()) if histogram else 1
    bar_width = 40
    for bucket, count in histogram.items():
        bar_len = int((count / max_count) * bar_width) if max_count else 0
        hist_table.add_row(bucket, str(count), "\u2588" * bar_len)
    con.print(hist_table)


def _print_stats_human(result: dict[str, Any], console: Console | None = None) -> None:
    con = console if console is not None else _console
    total = result["total"]
    if total == 0:
        con.print("No events recorded yet.")
        return

    con.rule("Vaudeville Stats", style="bold cyan")
    rules = result["rules"]
    if rules:
        _render_rules_table(con, rules, total)
    lat = result["latency"]
    _render_latency_summary(con, lat)
    _render_histogram(con, lat["histogram"])


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
