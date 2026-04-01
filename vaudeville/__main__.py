"""Vaudeville CLI entry point.

Usage: uv run python -m vaudeville <command>
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path


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

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "tail":
        cmd_tail(args)


if __name__ == "__main__":
    main()
