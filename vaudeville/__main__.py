"""Vaudeville CLI entry point.

Usage: uv run python -m vaudeville <command>
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _find_log_files() -> list[str]:
    """Find active vaudeville log files with a running daemon."""
    logs: list[str] = []
    for log_path in sorted(glob.glob("/tmp/vaudeville-*.log")):
        session_id = log_path.removeprefix("/tmp/vaudeville-").removesuffix(".log")
        pid_file = f"/tmp/vaudeville-{session_id}.pid"
        try:
            pid = int(Path(pid_file).read_text().strip())
            os.kill(pid, 0)  # check if alive
            logs.append(log_path)
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
            continue
    return logs


def cmd_tail(args: argparse.Namespace) -> None:
    """Tail active vaudeville daemon logs."""
    logs = _find_log_files()

    if not logs:
        print("[vaudeville] No active daemon found.", file=sys.stderr)
        print(
            "Start a Claude Code session with vaudeville hooks to spawn a daemon.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.session:
        target = f"/tmp/vaudeville-{args.session}.log"
        if not os.path.exists(target):
            print(f"[vaudeville] Log not found: {target}", file=sys.stderr)
            sys.exit(1)
        logs = [target]
    elif len(logs) > 1 and not args.all:
        print(f"[vaudeville] {len(logs)} active sessions found:", file=sys.stderr)
        for log in logs:
            print(f"  {log}", file=sys.stderr)
        print(
            "Use --all to tail all, or specify --session <id>.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        subprocess.run(["tail", "-f"] + logs)
    except KeyboardInterrupt:
        pass


_EVENTS_LOG = os.path.join(
    os.path.expanduser("~"), ".vaudeville", "logs", "events.jsonl"
)


def cmd_watch(args: argparse.Namespace) -> None:
    """Launch the live watch TUI."""
    from vaudeville.server.watch import watch

    try:
        watch(log_path=args.log_path)
    except KeyboardInterrupt:
        pass


def cmd_stats(args: argparse.Namespace) -> None:
    """Print aggregated classification statistics."""
    from vaudeville.server.stats import aggregate_events

    result = aggregate_events(args.log_path)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    _print_stats_human(result)


def _print_stats_human(result: dict[str, Any]) -> None:
    """Format stats as human-readable text."""
    total = result["total"]
    if total == 0:
        print("No events recorded yet.")
        return

    tr = result["time_range"]
    print(f"=== Vaudeville Stats ({tr['earliest']} — {tr['latest']}) ===")
    print()

    rules = result["rules"]
    if rules:
        # Column headers
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

    tail_parser = sub.add_parser("tail", help="Tail active daemon logs")
    session_group = tail_parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "--all", action="store_true", help="Tail all active sessions"
    )
    session_group.add_argument("--session", help="Tail a specific session ID")

    watch_parser = sub.add_parser("watch", help="Live TUI of rule firings")
    watch_parser.add_argument(
        "--log-path",
        default=_EVENTS_LOG,
        help="Path to events.jsonl (default: ~/.vaudeville/logs/events.jsonl)",
    )

    stats_parser = sub.add_parser("stats", help="Show classification statistics")
    stats_parser.add_argument("--json", action="store_true", help="Output raw JSON")
    stats_parser.add_argument(
        "--log-path",
        default=_EVENTS_LOG,
        help="Path to events.jsonl (default: ~/.vaudeville/logs/events.jsonl)",
    )

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "tail":
        cmd_tail(args)
    elif args.command == "watch":
        cmd_watch(args)
    elif args.command == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()
