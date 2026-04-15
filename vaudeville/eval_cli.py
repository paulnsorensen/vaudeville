"""CLI entrypoint for the vaudeville eval harness."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .core.rules import load_rules, load_rules_layered
from .eval import (
    CaseResult,
    EvalCase,
    _load_test_file,
    load_test_cases,
)
from .server import InferenceBackend


def _find_project_root() -> str | None:
    """Find the git working tree root, or None if not in a repo."""
    import subprocess

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


def _build_backend(args: argparse.Namespace) -> InferenceBackend:
    """Initialize the inference backend from CLI args.

    Prefers a warm daemon over in-process MLXBackend unless --no-daemon.
    """
    no_daemon = getattr(args, "no_daemon", False)
    if not no_daemon:
        from .server.daemon_backend import DaemonBackend, daemon_is_alive

        if daemon_is_alive():
            print("Using warm daemon for inference")
            return DaemonBackend()
        logging.warning(
            "[vaudeville] Daemon not available — falling back to in-process MLXBackend"
        )

    from .server import MLXBackend

    print(f"Loading model: {args.model}")
    return MLXBackend(args.model)


def _build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Vaudeville rule eval harness")
    parser.add_argument("--rule", help="Evaluate only this rule")
    parser.add_argument(
        "--cross-validate",
        action="store_true",
        help="Leave-one-out cross-validation with per-fold output",
    )
    parser.add_argument(
        "--test-file",
        help="Extra test file to include (YAML format)",
    )
    parser.add_argument(
        "--threshold-sweep",
        action="store_true",
        help="Sweep thresholds 0.30-0.90 and print confusion matrix per threshold",
    )
    parser.add_argument(
        "--calibrate",
        metavar="RULE",
        help="Calibrate threshold for a rule: sweep, pick F1-optimal, write to YAML",
    )
    parser.add_argument(
        "--eval-log",
        help="Path to JSONL file for regression tracking (appends one line per run)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit per-case JSONL output instead of summary text",
    )
    parser.add_argument(
        "--rules-dir",
        help="Load rules from this directory only (skip layered resolution)",
    )
    parser.add_argument(
        "--no-daemon",
        action="store_true",
        help="Force in-process backend, skip daemon check",
    )
    parser.add_argument(
        "--backend", default="mlx", choices=["mlx"], help="Inference backend"
    )
    parser.add_argument(
        "--model",
        default="mlx-community/Phi-4-mini-instruct-4bit",
        help="Model path or Hugging Face ID",
    )
    return parser


def _apply_extra_test_file(
    args: argparse.Namespace, test_suites: dict[str, list[EvalCase]]
) -> None:
    """Merge --test-file cases into the matching test suite (mutates in place)."""
    if not (args.test_file and args.rule):
        return
    extra_cases, rule_name = _load_test_file(args.test_file)
    if rule_name != args.rule:
        print(
            f"Error: --test-file specifies rule '{rule_name}' but --rule is '{args.rule}'"
        )
        sys.exit(1)
    existing = test_suites.get(args.rule, [])
    test_suites[args.rule] = existing + extra_cases


def _tests_dir() -> str:
    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    return os.path.join(plugin_root, "tests")


def _emit_jsonl(case_results: list[CaseResult]) -> None:
    """Write per-case results as JSONL to stdout."""
    import json

    for cr in case_results:
        print(json.dumps(cr.to_jsonl_dict()))


def main() -> None:
    args = _build_parser().parse_args()
    backend = _build_backend(args)
    if getattr(args, "rules_dir", None):
        rules = load_rules(args.rules_dir)
    else:
        rules = load_rules_layered(project_root=_find_project_root())
    test_suites = load_test_cases(_tests_dir())

    _apply_extra_test_file(args, test_suites)

    if args.calibrate:
        from .eval_report import run_calibrate

        run_calibrate(args, rules, test_suites, backend, _find_project_root())

    if args.rule:
        test_suites = {k: v for k, v in test_suites.items() if k == args.rule}
        if not test_suites:
            print(f"No test suite found for rule: {args.rule}")
            sys.exit(1)

    from .eval_report import run_evaluations, threshold_sweep, write_eval_log

    passed, all_results, all_case_results = run_evaluations(
        args, rules, test_suites, backend
    )

    if args.json:
        _emit_jsonl(all_case_results)

    if args.eval_log and all_results:
        write_eval_log(args.eval_log, args.model, all_results)
        print(f"\nEval log appended to {args.eval_log}")

    if args.threshold_sweep:
        threshold_sweep(test_suites, rules, backend)

    if not args.json:
        print("\n" + ("ALL RULES PASS" if passed else "SOME RULES FAILED"))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
