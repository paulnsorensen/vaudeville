"""Rule-abandonment side effects: rule file mutation and metric extraction."""

from __future__ import annotations

import datetime
import json
import re
import subprocess
from pathlib import Path

from vaudeville.orchestrator._phase import Thresholds


def _locate_rule_file(rule_name: str, rules_dir: str) -> Path:
    base = Path(rules_dir)
    for ext in (".yaml", ".yml"):
        c = base / f"{rule_name}{ext}"
        if c.exists():
            return c
    raise FileNotFoundError(f"rule {rule_name!r} not found in {rules_dir}")


def abandon_rule(
    rule_name: str, reason: str, metrics: dict[str, object], rules_dir: str
) -> None:
    """Disable rule tier, append ABANDONED comment, and log to abandoned.jsonl."""
    rule_file = _locate_rule_file(rule_name, rules_dir)
    content = rule_file.read_text()

    sanitized = reason.replace("\n", " ").replace("\r", " ")
    ts = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")

    new_content, count = re.subn(
        r"^tier:\s*\S+", "tier: disabled", content, flags=re.MULTILINE
    )
    if count == 0:
        separator = "" if not content or content.endswith("\n") else "\n"
        new_content = content + separator + "tier: disabled\n"

    new_content += f"\n# ABANDONED {ts}: {sanitized}\n"
    rule_file.write_text(new_content)

    log_dir = Path(rules_dir).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_entry = json.dumps(
        {"ts": ts, "rule": rule_name, "reason": sanitized, "metrics": metrics}
    )
    with open(log_dir / "abandoned.jsonl", "a") as f:
        f.write(log_entry + "\n")


def _extract_abandon_reason(judge_stdout: str) -> str:
    """Return prose appearing above the final JUDGE_* signal in stdout."""
    lines = judge_stdout.splitlines()
    last_judge_idx = next(
        (
            i
            for i in range(len(lines) - 1, -1, -1)
            if lines[i].strip().startswith("JUDGE_")
        ),
        None,
    )
    if last_judge_idx is None:
        return "\n".join(lines).strip()[-2000:]
    return "\n".join(lines[:last_judge_idx]).strip()[-2000:]


def _eval_rule(rule_name: str, project_root: str) -> Thresholds | None:
    try:
        out = subprocess.run(
            ["uv", "run", "python", "-m", "vaudeville.eval_cli", "--rule", rule_name],
            capture_output=True,
            text=True,
            cwd=project_root,
            check=False,
        ).stdout
    except FileNotFoundError:
        return None
    try:
        return Thresholds(
            p_min=float(re.search(r"precision=([\d.]+)", out).group(1)),  # type: ignore[union-attr]
            r_min=float(re.search(r"recall=([\d.]+)", out).group(1)),  # type: ignore[union-attr]
            f1_min=float(re.search(r"f1=([\d.]+)", out).group(1)),  # type: ignore[union-attr]
        )
    except (AttributeError, ValueError):
        return None


def abandon_with_metrics(
    rule_name: str,
    judge_stdout: str,
    raw_line: str,
    project_root: str,
    rules_dir: str,
) -> None:
    reason = _extract_abandon_reason(judge_stdout) or raw_line
    metrics: dict[str, object] = {}
    eval_result = _eval_rule(rule_name, project_root)
    if eval_result:
        metrics = {
            "p_min": eval_result.p_min,
            "r_min": eval_result.r_min,
            "f1_min": eval_result.f1_min,
        }
    abandon_rule(rule_name, reason, metrics, rules_dir)
