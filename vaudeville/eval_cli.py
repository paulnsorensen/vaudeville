"""CLI entrypoint for the vaudeville eval harness."""

from __future__ import annotations

import argparse
import sys

from .core.paths import find_project_root
from .eval import CaseResult, load_test_cases
from .rules import DecideTestCase, load_rules, load_rules_layered
from .server.user_config import load_user_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vaudeville rule eval harness")
    parser.add_argument("--rule", help="Evaluate only this rule")
    parser.add_argument(
        "--cross-validate",
        action="store_true",
        help="Leave-one-out cross-validation with per-fold output",
    )
    parser.add_argument(
        "--calibrate",
        metavar="RULE",
        help="Deferred to FU-1b (pydantic-evals); prints a notice and exits 0",
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
    return parser


def _emit_jsonl(case_results: list[CaseResult]) -> None:
    import json
    from dataclasses import asdict

    for cr in case_results:
        print(json.dumps(asdict(cr)))


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.json and args.cross_validate:
        parser.error("--json cannot be combined with --cross-validate")

    if getattr(args, "rules_dir", None):
        rules = load_rules(args.rules_dir)
    else:
        rules = load_rules_layered(project_root=find_project_root()).by_name()
    test_suites: dict[str, list[DecideTestCase]] = load_test_cases(rules)

    if args.calibrate:
        from .eval_calibrate import run_calibrate

        run_calibrate(args.calibrate)
        sys.exit(0)

    if args.rule:
        test_suites = {k: v for k, v in test_suites.items() if k == args.rule}
        if not test_suites:
            print(f"No test suite found for rule: {args.rule}")
            sys.exit(1)

    config = load_user_config()
    no_model_configured = config.default_model is None and not config.providers
    if no_model_configured:
        print(
            "vaudeville: no model configured (~/.vaudeville/config); "
            "all decisions will fail open",
            file=sys.stderr,
        )

    from .eval_report import run_evaluations

    passed, _all_results, all_case_results = run_evaluations(
        args, rules, test_suites, config
    )

    if args.json:
        _emit_jsonl(all_case_results)

    if no_model_configured:
        # A fail-open run classifies nothing; there is no pass/fail gate to apply.
        if not args.json:
            print("\nNo model configured: skipped the pass/fail gate")
        sys.exit(0)

    if not args.json:
        print("\n" + ("ALL RULES PASS" if passed else "SOME RULES FAILED"))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
