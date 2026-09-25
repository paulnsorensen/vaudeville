"""CLI entrypoint for the vaudeville eval harness."""

from __future__ import annotations

import argparse
import sys

from pydantic_ai.models import Model

from .core.paths import find_project_root
from .eval import load_test_cases
from .rules import DecideTestCase, bundled_rules_dir, load_rules, load_rules_layered
from .server.user_config import load_user_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vaudeville rule eval harness")
    parser.add_argument("--rule", help="Evaluate only this rule")
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Print a confidence-calibration report for --rule and exit 0",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON run-summary object on stdout instead of summary text",
    )
    parser.add_argument(
        "--rules-dir",
        help="Load rules from this directory only (skip layered resolution)",
    )
    return parser


def main(*, model_override: Model | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if getattr(args, "rules_dir", None):
        rules = load_rules(args.rules_dir)
    else:
        bundled_dir = bundled_rules_dir()
        bundled_rules = load_rules(bundled_dir) if bundled_dir else {}
        layered_rules = load_rules_layered(project_root=find_project_root()).by_name()
        rules = {**bundled_rules, **layered_rules}
    test_suites: dict[str, list[DecideTestCase]] = load_test_cases(rules)

    if args.rule:
        test_suites = {k: v for k, v in test_suites.items() if k == args.rule}
        if not test_suites:
            print(f"No test suite found for rule: {args.rule}")
            sys.exit(1)

    config = load_user_config()

    if args.calibrate:
        if not args.rule:
            parser.error("--calibrate requires --rule")
        from .eval import evaluate_rule
        from .eval_calibrate import calibrate, format_calibration

        _results, case_results = evaluate_rule(
            args.rule, test_suites[args.rule], rules, config, model_override=model_override
        )
        print(format_calibration(args.rule, calibrate(case_results)))
        sys.exit(0)

    no_model_configured = config.default_model is None and not config.providers
    if no_model_configured:
        print(
            "vaudeville: no model configured (~/.vaudeville/config); all decisions will fail open",
            file=sys.stderr,
        )

    from .eval_report import build_run_summary, run_evaluations

    out = sys.stderr if args.json else sys.stdout
    passed, all_results, all_case_results = run_evaluations(
        rules, test_suites, config, model_override=model_override, file=out
    )
    summary = build_run_summary(all_results, all_case_results)

    if args.json:
        print(summary.to_json())
    else:
        print("\n" + ("ALL RULES PASS" if passed else "SOME RULES FAILED"))

    if no_model_configured:
        # A fail-open run classifies nothing; there is no pass/fail gate to apply.
        if not args.json:
            print("\nNo model configured: skipped the pass/fail gate")
        sys.exit(0)

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
