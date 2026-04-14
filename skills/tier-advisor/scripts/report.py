#!/usr/bin/env python3
"""Generate tier-advisor recommendation report.

Reads analysis output (from analyze.py via stdin or by running it),
applies promotion/demotion thresholds, and outputs grouped markdown.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RULES_DEV_DIR = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", ".")) / "rules_dev"

THRESHOLDS = {
    "shadow_to_warn": {
        "min_evals": 50,
        "min_agreement": 0.70,
        "violation_rate_min": 0.02,
        "violation_rate_max": 0.40,
    },
    "warn_to_block": {
        "min_evals": 200,
        "min_agreement": 0.85,
        "violation_rate_min": 0.05,
        "violation_rate_max": 0.30,
        "min_p50_confidence": 0.7,
    },
    "demote_warn_to_shadow": {
        "max_agreement": 0.50,
        "max_violation_rate": 0.60,
    },
}


def get_current_tier(rule_name: str) -> str:
    yaml_path = RULES_DEV_DIR / f"{rule_name}.yaml"
    if not yaml_path.exists():
        return "unknown"
    with open(yaml_path) as f:
        for line in f:
            if line.startswith("tier:"):
                return line.split(":", 1)[1].strip()
    return "shadow"


def classify(rule: dict) -> tuple[str, str]:
    """Return (recommendation, reason) for a rule."""
    tier = get_current_tier(rule["rule"])
    total = rule["total_evals"]
    vr = rule["violation_rate"]
    agreement = rule.get("agreement_rate")
    p50 = rule.get("p50_confidence", 0.0)
    evaluated = rule.get("agreement_evaluated", 0)

    if total < 10:
        return "insufficient-data", f"Only {total} evals recorded."

    if tier == "warn":
        t = THRESHOLDS["demote_warn_to_shadow"]
        if agreement is not None and agreement < t["max_agreement"]:
            return (
                "demote",
                f"Agreement {agreement:.0%} below {t['max_agreement']:.0%} threshold.",
            )
        if vr > t["max_violation_rate"]:
            return (
                "demote",
                f"Violation rate {vr:.0%} exceeds {t['max_violation_rate']:.0%} ceiling.",
            )

        t = THRESHOLDS["warn_to_block"]
        if (
            total >= t["min_evals"]
            and (agreement is not None and agreement >= t["min_agreement"])
            and t["violation_rate_min"] <= vr <= t["violation_rate_max"]
            and p50 >= t["min_p50_confidence"]
        ):
            return "promote-to-block", (
                f"{total} evals, {agreement:.0%} agreement, "
                f"{vr:.0%} violation rate, p50 confidence {p50:.2f}."
            )

        return "hold-at-warn", f"{total} evals, {vr:.0%} violation rate."

    if tier == "shadow":
        t = THRESHOLDS["shadow_to_warn"]
        if total < t["min_evals"]:
            return (
                "insufficient-data",
                f"{total}/{t['min_evals']} evals needed for promotion.",
            )

        if agreement is not None and agreement < t["min_agreement"]:
            return (
                "hold-at-shadow",
                f"Agreement {agreement:.0%} below {t['min_agreement']:.0%} threshold.",
            )

        if not (t["violation_rate_min"] <= vr <= t["violation_rate_max"]):
            return (
                "hold-at-shadow",
                f"Violation rate {vr:.0%} outside [{t['violation_rate_min']:.0%}, {t['violation_rate_max']:.0%}].",
            )

        return "promote-to-warn", (
            f"{total} evals, "
            + (
                f"{agreement:.0%} agreement, "
                if agreement is not None
                else "no agreement data, "
            )
            + f"{vr:.0%} violation rate."
        )

    if evaluated == 0:
        return "insufficient-data", "No agreement data available."

    return "hold-at-shadow", f"Tier '{tier}' — manual review needed."


def format_report(rules: list[dict]) -> str:
    groups: dict[str, list[tuple[dict, str]]] = {
        "promote-to-block": [],
        "promote-to-warn": [],
        "demote": [],
        "hold-at-warn": [],
        "hold-at-shadow": [],
        "insufficient-data": [],
    }

    for r in rules:
        rec, reason = classify(r)
        groups.setdefault(rec, []).append((r, reason))

    lines = ["# Tier Advisor Report", ""]

    group_labels = {
        "promote-to-block": "Promote to Block",
        "promote-to-warn": "Promote to Warn",
        "demote": "Demote",
        "hold-at-warn": "Hold at Warn",
        "hold-at-shadow": "Hold at Shadow",
        "insufficient-data": "Insufficient Data",
    }

    for key, label in group_labels.items():
        entries = groups.get(key, [])
        if not entries:
            continue
        lines.append(f"## {label}")
        lines.append("")
        for rule, reason in entries:
            tier = get_current_tier(rule["rule"])
            lines.append(
                f"- **{rule['rule']}** (current: {tier}, "
                f"evals: {rule['total_evals']}, "
                f"violations: {rule['violations']}, "
                f"rate: {rule['violation_rate']:.1%})"
            )
            lines.append(f"  {reason}")
            lines.append("")

    return "\n".join(lines)


def main() -> None:
    stdin_data = ""
    if not sys.stdin.isatty():
        stdin_data = sys.stdin.read().strip()

    if stdin_data:
        data = json.loads(stdin_data)
    else:
        script_dir = Path(__file__).parent
        result = subprocess.run(
            [sys.executable, str(script_dir / "analyze.py")],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f"ERROR: analyze.py failed: {result.stderr[:200]}", file=sys.stderr)
            sys.exit(1)
        data = json.loads(result.stdout)

    report = format_report(data)
    print(report)


if __name__ == "__main__":
    main()
