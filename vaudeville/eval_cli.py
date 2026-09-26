"""CLI entrypoint for the vaudeville eval harness."""

from __future__ import annotations

import argparse
import sys

from pydantic_ai.models import Model

from .core.paths import find_project_root
from .eval import load_test_cases
from .rules import (
    DecideRule,
    DecideTestCase,
    RewriteRule,
    bundled_rules_dir,
    load_rules,
    load_rules_layered,
)
from .server.user_config import UserConfig, load_user_config


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


def _resolve_rules(args: argparse.Namespace) -> dict[str, DecideRule | RewriteRule]:
    if getattr(args, "rules_dir", None):
        return load_rules(args.rules_dir)
    bundled_dir = bundled_rules_dir()
    bundled_rules = load_rules(bundled_dir) if bundled_dir else {}
    layered_rules = load_rules_layered(project_root=find_project_root()).by_name()
    return {**bundled_rules, **layered_rules}


def _run_calibrate(
    test_suites: dict[str, list[DecideTestCase]],
    rules: dict[str, DecideRule | RewriteRule],
    config: UserConfig,
    *,
    model_override: Model | None,
) -> None:
    """Run and print the `--calibrate` report for the single rule in `test_suites`."""
    from .eval import evaluate_rule
    from .eval_calibrate import calibrate, format_calibration

    rule_name = next(iter(test_suites))
    _results, case_results = evaluate_rule(
        rule_name, test_suites[rule_name], rules, config, model_override=model_override
    )
    print(format_calibration(rule_name, calibrate(case_results)))


def main(*, model_override: Model | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.calibrate and args.json:
        parser.error("--calibrate is not compatible with --json")

    rules = _resolve_rules(args)
    test_suites: dict[str, list[DecideTestCase]] = load_test_cases(rules)

    if args.rule:
        test_suites = {k: v for k, v in test_suites.items() if k == args.rule}
        if not test_suites:
            print(f"No test suite found for rule: {args.rule}", file=sys.stderr)
            sys.exit(1)

    config = load_user_config()

    if args.calibrate:
        if not args.rule:
            parser.error("--calibrate requires --rule")
        _run_calibrate(test_suites, rules, config, model_override=model_override)
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

    if args.json:
        summary = build_run_summary(all_results, all_case_results)
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
