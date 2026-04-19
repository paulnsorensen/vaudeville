"""Vaudeville CLI entry point.

Usage: uv run python -m vaudeville <command>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any


_EVENTS_LOG = os.path.join(
    os.path.expanduser("~"), ".vaudeville", "logs", "events.jsonl"
)


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
    """Find the project root via git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return os.getcwd()


def cmd_tune(args: argparse.Namespace) -> None:
    """Run the rule tuning agent via ralphify.

    Launches an autonomous agent loop to iteratively improve a rule's
    prompt until it meets precision/recall/f1 thresholds.
    """
    project_root = _find_project_root()
    ralph_dir = os.path.join(project_root, "commands", "tune")

    if not os.path.exists(os.path.join(ralph_dir, "RALPH.md")):
        print(f"Error: RALPH.md not found in {ralph_dir}", file=sys.stderr)
        sys.exit(2)

    # Build the ralph run command
    cmd = [
        "ralph",
        "run",
        ralph_dir,
        "--target",
        f"Tune rule '{args.rule}' to meet thresholds: "
        f"p_min={args.p_min}, r_min={args.r_min}, f1_min={args.f1_min}",
    ]

    print(f"Starting tune agent for rule: {args.rule}")
    print(
        f"Thresholds: precision>={args.p_min}, recall>={args.r_min}, f1>={args.f1_min}"
    )
    print()

    try:
        result = subprocess.run(cmd, cwd=project_root)
        sys.exit(result.returncode)
    except FileNotFoundError:
        print(
            "Error: 'ralph' CLI not found. Install ralphify first:\n"
            "  pip install ralphify",
            file=sys.stderr,
        )
        sys.exit(2)
    except KeyboardInterrupt:
        print("\nTuning interrupted.")
        sys.exit(1)


def cmd_generate(args: argparse.Namespace) -> None:
    """Run the rule generation agent via ralphify.

    Launches an autonomous agent loop to create a new rule from
    user instructions, iterating until it meets metric thresholds.
    """
    project_root = _find_project_root()
    ralph_dir = os.path.join(project_root, "commands", "generate")

    if not os.path.exists(os.path.join(ralph_dir, "RALPH.md")):
        print(f"Error: RALPH.md not found in {ralph_dir}", file=sys.stderr)
        sys.exit(2)

    mode = "live" if args.live else "shadow"

    # Build the ralph run command
    cmd = [
        "ralph",
        "run",
        ralph_dir,
        "--target",
        f"Generate rule from instructions: '{args.instructions}'. "
        f"Thresholds: p_min={args.p_min}, r_min={args.r_min}, f1_min={args.f1_min}. "
        f"Mode: {mode}.",
    ]

    print("Starting generate agent")
    print(f"Instructions: {args.instructions}")
    print(
        f"Thresholds: precision>={args.p_min}, recall>={args.r_min}, f1>={args.f1_min}"
    )
    print(f"Mode: {mode}")
    print()

    try:
        result = subprocess.run(cmd, cwd=project_root)
        sys.exit(result.returncode)
    except FileNotFoundError:
        print(
            "Error: 'ralph' CLI not found. Install ralphify first:\n"
            "  pip install ralphify",
            file=sys.stderr,
        )
        sys.exit(2)
    except KeyboardInterrupt:
        print("\nGeneration interrupted.")
        sys.exit(1)


def _print_stats_human(result: dict[str, Any]) -> None:
    total = result["total"]
    if total == 0:
        print("No events recorded yet.")
        return

    tr = result["time_range"]
    print(f"=== Vaudeville Stats ({tr['earliest']} — {tr['latest']}) ===")
    print()

    rules = result["rules"]
    if rules:
        hdr = (
            f"{'Rule':<30} {'Total':>6} {'Violations':>11} {'Pass %':>7} {'Avg ms':>7}"
        )
        print(hdr)
        print("-" * len(hdr))
        for name, data in rules.items():
            print(
                f"{name:<30} {data['total']:>6} {data['violations']:>11}"
                f" {data['pass_rate']:>6.1f}% {data['avg_latency_ms']:>7.1f}"
            )
        print()

    lat = result["latency"]
    print(
        f"Latency: p50={lat['p50_ms']:.1f}ms  p95={lat['p95_ms']:.1f}ms  mean={lat['mean_ms']:.1f}ms"
    )
    print()

    print("Histogram:")
    for bucket, count in lat["histogram"].items():
        bar = "#" * min(count, 50)
        print(f"  {bucket:>10}: {count:>5}  {bar}")
    print()

    print(f"Total classifications: {total}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vaudeville",
        description="Vaudeville SLM hook enforcement",
    )
    sub = parser.add_subparsers(dest="command")

    watch_parser = sub.add_parser("watch", help="Live TUI of rule firings")
    watch_parser.add_argument(
        "--log-path",
        default=_EVENTS_LOG,
        help="Path to events.jsonl (default: ~/.vaudeville/logs/events.jsonl)",
    )

    sub.add_parser("setup", help="Download model and verify inference")

    stats_parser = sub.add_parser("stats", help="Show classification statistics")
    stats_parser.add_argument("--json", action="store_true", help="Output raw JSON")
    stats_parser.add_argument(
        "--log-path",
        default=_EVENTS_LOG,
        help="Path to events.jsonl (default: ~/.vaudeville/logs/events.jsonl)",
    )

    # tune command - ralphify-based rule tuning
    tune_parser = sub.add_parser(
        "tune",
        help="Tune a rule to meet precision/recall/f1 thresholds (autonomous agent)",
    )
    tune_parser.add_argument("rule", help="Rule name to tune")
    tune_parser.add_argument(
        "--p-min",
        type=float,
        default=0.95,
        help="Minimum precision threshold (default: 0.95)",
    )
    tune_parser.add_argument(
        "--r-min",
        type=float,
        default=0.80,
        help="Minimum recall threshold (default: 0.80)",
    )
    tune_parser.add_argument(
        "--f1-min",
        type=float,
        default=0.85,
        help="Minimum F1 threshold (default: 0.85)",
    )

    # generate command - ralphify-based rule generation
    generate_parser = sub.add_parser(
        "generate",
        help="Generate a new rule from instructions (autonomous agent)",
    )
    generate_parser.add_argument(
        "instructions",
        help="Description of what the rule should detect",
    )
    generate_parser.add_argument(
        "--p-min",
        type=float,
        default=0.95,
        help="Minimum precision threshold (default: 0.95)",
    )
    generate_parser.add_argument(
        "--r-min",
        type=float,
        default=0.80,
        help="Minimum recall threshold (default: 0.80)",
    )
    generate_parser.add_argument(
        "--f1-min",
        type=float,
        default=0.85,
        help="Minimum F1 threshold (default: 0.85)",
    )
    generate_parser.add_argument(
        "--live",
        action="store_true",
        help="Commit the rule when thresholds are met (default: shadow mode)",
    )

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "watch":
        cmd_watch(args)
    elif args.command == "setup":
        cmd_setup(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "tune":
        cmd_tune(args)
    elif args.command == "generate":
        cmd_generate(args)


if __name__ == "__main__":
    main()
