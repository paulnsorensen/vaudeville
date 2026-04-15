"""CLI entry point for `vaudeville tune <rule>`.

Exit codes: 0 = pass, 1 = fail-but-completed, 2 = harness error.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time

from ..core.paths import PID_FILE, SOCKET_PATH
from ..core.rules import load_rules_layered
from ..eval import EvalCase, load_test_cases
from ..server.daemon_backend import DaemonBackend, daemon_is_alive
from ..server.inference import InferenceBackend
from .harness import StudyConfig, _format_verdict, run_study
from .split import split_cases

logger = logging.getLogger(__name__)

DAEMON_STARTUP_TIMEOUT = 10.0
DAEMON_POLL_INTERVAL = 0.3

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaudeville tune",
        description="Tune a vaudeville rule via Optuna study",
    )
    parser.add_argument("rule", help="Rule name to tune")
    parser.add_argument(
        "--p-min",
        type=float,
        default=0.95,
        help="Minimum precision (default: 0.95)",
    )
    parser.add_argument(
        "--r-min",
        type=float,
        default=0.80,
        help="Minimum recall (default: 0.80)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=15,
        help="Max trials (default: 15)",
    )
    parser.add_argument(
        "--no-daemon", action="store_true", help="Force in-process backend"
    )
    parser.add_argument(
        "--author",
        action="store_true",
        help="Enable LLM candidate authoring during study",
    )
    return parser


def _find_project_root() -> str | None:
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
    return None


def _try_start_daemon() -> bool:
    """Attempt to spawn the daemon and wait for it to become ready."""
    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )
    cmd = [
        sys.executable,
        "-m",
        "vaudeville.server",
        "--socket",
        SOCKET_PATH,
        "--pid-file",
        PID_FILE,
    ]
    try:
        subprocess.Popen(
            cmd,
            cwd=plugin_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logger.warning("Failed to spawn daemon process")
        return False

    deadline = time.monotonic() + DAEMON_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(DAEMON_POLL_INTERVAL)
        if daemon_is_alive():
            return True
    logger.warning("Daemon did not become ready within %.0fs", DAEMON_STARTUP_TIMEOUT)
    return False


def _build_backend(no_daemon: bool) -> InferenceBackend:
    """Build inference backend, preferring warm daemon."""
    if not no_daemon and daemon_is_alive():
        print("Using warm daemon for inference")
        return DaemonBackend()
    if not no_daemon:
        print("Daemon not running — attempting auto-start…")
        if _try_start_daemon():
            print("Daemon started successfully")
            return DaemonBackend()
        print("Auto-start failed — falling back to in-process MLXBackend")
    from ..server import MLXBackend

    return MLXBackend("mlx-community/Phi-4-mini-instruct-4bit")


def _load_cases_for_rule(rule_name: str) -> list[EvalCase]:
    """Load test cases for a specific rule from the tests directory."""
    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )
    tests_dir = os.path.join(plugin_root, "tests")
    suites = load_test_cases(tests_dir)
    return suites.get(rule_name, [])


def _get_test_file_mtime(rule_name: str) -> float:
    """Get mtime of the test file for deterministic splitting."""
    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )
    tests_dir = os.path.join(plugin_root, "tests")
    for ext in (".yaml", ".yml"):
        path = os.path.join(tests_dir, f"{rule_name}{ext}")
        if os.path.exists(path):
            return os.path.getmtime(path)
    return 0.0


def run_tune(args: argparse.Namespace) -> int:
    """Run the tune pipeline. Returns exit code."""
    rule_name = args.rule
    rules = load_rules_layered(project_root=_find_project_root())

    if rule_name not in rules:
        print(f"Rule not found: {rule_name}", file=sys.stderr)
        return EXIT_ERROR

    rule = rules[rule_name]
    cases = _load_cases_for_rule(rule_name)
    if not cases:
        print(f"No test cases for rule: {rule_name}", file=sys.stderr)
        return EXIT_ERROR

    mtime = _get_test_file_mtime(rule_name)
    tune_cases, held_cases = split_cases(cases, rule_name, mtime)

    config = StudyConfig(
        rule_name=rule_name,
        p_min=args.p_min,
        r_min=args.r_min,
        budget=args.budget,
        author=args.author,
    )

    backend = _build_backend(args.no_daemon)
    verdict = run_study(rule, tune_cases, held_cases, backend, config)
    print(_format_verdict(verdict))

    return EXIT_PASS if verdict.passed else EXIT_FAIL


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()
    try:
        code = run_tune(args)
    except Exception as e:
        print(f"Harness error: {e}", file=sys.stderr)
        code = EXIT_ERROR
    sys.exit(code)
