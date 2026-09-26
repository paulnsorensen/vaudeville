"""Adversarial contract tests for the `unsure:` gate (AC-6 to AC-9).

Attack vectors: range and type edges of `unsure.below`, load-time
rejection through the rule loader, a zero (falsy) confidence, gated
substitutes that are themselves `escalate` or `allow`, an outcome with no
`on:` entry, cross-rule precedence of a substituted action, a None
confidence outside the outcome filter, and an escalate target that also
fires at the top level of the same request.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import make_request as _request
from _hook_helpers import write_rule as _write_rule
from pydantic import ValidationError

from vaudeville.rules import DecideRule, load_rules, parse_rule
from vaudeville.server.agents import DecideResult
from vaudeville.server.event_log import EventLogger
from vaudeville.server.hook import handle_hook_request
from vaudeville.server.hook.pipeline import DecideFn
from vaudeville.server.log_config import LogConfig
from vaudeville.server.user_config import UserConfig

_GATED_RULE: dict[str, Any] = {
    "type": "decide",
    "name": "gated",
    "event": "PreToolUse",
    "matcher": "Write",
    "model": "typesafe:jev-1.13",
    "prompt": "Classify.",
    "outcomes": ["violation", "clean"],
    "on": {"violation": "block"},
    "tier": "block",
    "unsure": {"below": 0.7, "action": "warn", "outcomes": ["violation"]},
}


def _gated(**overrides: Any) -> dict[str, Any]:
    rule = dict(_GATED_RULE)
    rule.update(overrides)
    return rule


def _write(tmp_path: Path, rule: dict[str, Any]) -> None:
    _write_rule(tmp_path, str(rule["name"]), yaml.safe_dump(rule, sort_keys=False))


class _ScriptedDecide:
    """Returns a fixed `(outcome, confidence)` per rule name and records calls."""

    def __init__(self, script: dict[str, tuple[str, float | None]]) -> None:
        self.script = script
        self.calls: list[str] = []

    def __call__(self, rule: DecideRule, config: UserConfig, text: str) -> DecideResult:
        del config, text
        self.calls.append(rule.name)
        outcome, confidence = self.script[rule.name]
        return DecideResult(outcome=outcome, confidence=confidence)


def _run(tmp_path: Path, decide_fn: DecideFn) -> tuple[dict[str, object], list[dict[str, Any]]]:
    logs_dir = tmp_path / "logs"
    logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
    try:
        result = handle_hook_request(
            _request(tmp_path), config=_CONFIG, decide_fn=decide_fn, event_logger=logger
        )
    finally:
        logger.close()
    time.sleep(0.05)
    events = logs_dir / "events.jsonl"
    rows = (
        [json.loads(line) for line in events.read_text().strip().splitlines()]
        if events.exists()
        else []
    )
    return result, rows


def _row(rows: list[dict[str, Any]], rule: str) -> dict[str, Any]:
    matches = [r for r in rows if r["rule"] == rule]
    assert len(matches) == 1, f"expected one log row for {rule!r}, got {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# AC-6: load-time validation edges
# ---------------------------------------------------------------------------


class TestUnsureLoadEdges:
    def test_below_exactly_one_is_accepted(self) -> None:
        """The (0, 1] interval is closed at 1."""
        rule = parse_rule(_gated(unsure={"below": 1.0, "action": "warn"}))
        assert isinstance(rule, DecideRule)
        assert rule.unsure is not None
        assert rule.unsure.below == 1.0

    @pytest.mark.parametrize("below", [-0.1, math.nan, math.inf, 1.0000001])
    def test_below_outside_interval_rejected_with_rule_name(self, below: float) -> None:
        with pytest.raises(ValidationError, match="'gated'") as exc_info:
            parse_rule(_gated(unsure={"below": below, "action": "warn"}))
        assert "below" in str(exc_info.value)

    def test_one_bad_entry_among_good_outcome_filter_entries_rejected(self) -> None:
        with pytest.raises(ValidationError, match="'gated'") as exc_info:
            parse_rule(
                _gated(unsure={"below": 0.5, "action": "warn", "outcomes": ["violation", "vio"]})
            )
        assert "'vio'" in str(exc_info.value)

    def test_below_bool_rejected(self) -> None:
        """A YAML `true` must not silently pass as 1.0."""
        with pytest.raises(ValidationError) as exc_info:
            parse_rule(_gated(unsure={"below": True, "action": "warn"}))
        assert "bool" in str(exc_info.value)

    def test_below_int_one_is_accepted(self) -> None:
        """An int is not a bool; 1 still means the closed upper bound."""
        rule = parse_rule(_gated(unsure={"below": 1, "action": "warn"}))
        assert isinstance(rule, DecideRule)
        assert rule.unsure is not None
        assert rule.unsure.below == 1.0

    def test_unknown_unsure_key_rejected(self) -> None:
        """A typo such as `threshold:` must not load as a gate with defaults."""
        with pytest.raises(ValidationError):
            parse_rule(_gated(unsure={"below": 0.5, "action": "warn", "threshold": 0.9}))

    def test_loader_skips_invalid_unsure_rule_and_names_it(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Rejection through the directory loader: the bad rule is absent,
        its sibling still loads, and the logged error names the rule."""
        (tmp_path / "good.yaml").write_text(yaml.safe_dump(_gated(name="good-gate")))
        (tmp_path / "bad.yaml").write_text(
            yaml.safe_dump(_gated(name="bad-gate", model="anthropic:claude-haiku-4-5"))
        )
        with caplog.at_level("WARNING"):
            rules = load_rules(str(tmp_path))
        assert set(rules) == {"good-gate"}
        assert "'bad-gate'" in caplog.text
        assert "typesafe" in caplog.text


# ---------------------------------------------------------------------------
# AC-7: dispatch of the substituted action
# ---------------------------------------------------------------------------


class TestUnsureDispatchEdges:
    def test_zero_confidence_gates_and_logs_zero_not_null(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0.0 is falsy; the gate and the log must still treat it as a value."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _write(tmp_path, _gated())

        result, rows = _run(tmp_path, _ScriptedDecide({"gated": ("violation", 0.0)}))

        assert "deny" not in str(result["stdout"])
        row = _row(rows, "gated")
        assert row["action"] == "warn"
        assert row["confidence"] == 0.0
        assert row["unsure"] is True
        assert row["unsure_below"] == 0.7
        assert row["confidence_missing"] is False

    @pytest.mark.parametrize("case", [(0.9999, "warn", True), (1.0, "block", False)])
    def test_below_one_gates_everything_short_of_certainty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: tuple[float, str, bool]
    ) -> None:
        confidence, expected_action, unsure = case
        monkeypatch.setenv("FAKE_KEY", "x")
        _write(tmp_path, _gated(unsure={"below": 1.0, "action": "warn"}))

        _, rows = _run(tmp_path, _ScriptedDecide({"gated": ("violation", confidence)}))

        row = _row(rows, "gated")
        assert row["action"] == expected_action
        assert row["unsure"] is unsure

    def test_unfiltered_gate_replaces_implicit_allow_of_unmapped_outcome(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`clean` has no `on:` entry (implicit allow); an unfiltered gate
        still substitutes its action for it."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _write(tmp_path, _gated(unsure={"below": 0.7, "action": "block"}))

        result, rows = _run(tmp_path, _ScriptedDecide({"gated": ("clean", 0.2)}))

        assert "deny" in str(result["stdout"])
        row = _row(rows, "gated")
        assert row["action"] == "block"
        assert row["unsure"] is True

    def test_gate_action_allow_releases_a_block_and_is_still_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write(tmp_path, _gated(unsure={"below": 0.7, "action": "allow"}))

        result, rows = _run(tmp_path, _ScriptedDecide({"gated": ("violation", 0.3)}))

        assert result == {"stdout": "", "exit_code": 0}
        row = _row(rows, "gated")
        assert row["action"] == "allow"
        assert row["verdict"] == "violation"
        assert row["confidence"] == 0.3
        assert row["unsure"] is True
        assert row["unsure_below"] == 0.7

    def test_gate_action_escalate_runs_target_and_dispatches_its_action(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A gated `escalate` substitute runs the escalate hop; the target's
        own `on:` action dispatches and the target's gate is skipped (AC-9)."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _write(
            tmp_path,
            _gated(
                on={"violation": "warn"},
                unsure={"below": 0.7, "action": {"action": "escalate", "rule": "reviewer"}},
            ),
        )
        # An escalate target must match the live event, so `reviewer` also
        # fires top-level, where its own gate releases it to allow.
        _write(tmp_path, _gated(name="reviewer", unsure={"below": 0.99, "action": "allow"}))
        decide_fn = _ScriptedDecide({"gated": ("violation", 0.4), "reviewer": ("violation", 0.1)})

        result, rows = _run(tmp_path, decide_fn)

        assert sorted(decide_fn.calls) == ["gated", "reviewer"]
        assert "deny" in str(result["stdout"])
        row = _row(rows, "gated")
        assert row["action"] == "block"
        assert row["unsure"] is True
        assert _row(rows, "reviewer")["action"] == "allow"

    def test_substituted_block_wins_precedence_over_sibling_warn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The substitute joins the precedence merge like any `on:` action."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _write(
            tmp_path,
            _gated(on={"violation": "warn"}, unsure={"below": 0.7, "action": "block"}),
        )
        sibling = _gated(name="sibling", model="fake:model", on={"violation": "warn"})
        del sibling["unsure"]
        _write(tmp_path, sibling)
        decide_fn = _ScriptedDecide({"gated": ("violation", 0.5), "sibling": ("violation", 0.5)})

        result, rows = _run(tmp_path, decide_fn)

        assert "deny" in str(result["stdout"])
        assert [r["action"] for r in rows if r["rule"] == "gated"] == ["block"]


# ---------------------------------------------------------------------------
# AC-8: None confidence
# ---------------------------------------------------------------------------


class TestConfidenceMissingEdges:
    def test_none_confidence_outside_filter_still_logs_confidence_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write(tmp_path, _gated(on={"violation": "block", "clean": "warn"}))

        _, rows = _run(tmp_path, _ScriptedDecide({"gated": ("clean", None)}))

        row = _row(rows, "gated")
        assert row["action"] == "warn"
        assert row["confidence"] is None
        assert row["confidence_missing"] is True
        assert row["unsure"] is False

    def test_rule_without_gate_never_reports_confidence_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        plain = _gated(model="fake:model", on={"violation": "warn"})
        del plain["unsure"]
        _write(tmp_path, plain)

        _, rows = _run(tmp_path, _ScriptedDecide({"gated": ("violation", None)}))

        row = _row(rows, "gated")
        assert row["confidence"] is None
        assert row["confidence_missing"] is False
        assert row["unsure"] is False


# ---------------------------------------------------------------------------
# AC-9: escalate hop
# ---------------------------------------------------------------------------


class TestEscalateHopEdges:
    def test_target_gate_applies_top_level_but_not_in_the_hop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One request, one target decide: the target's top-level evaluation
        uses its gate (warn), while the hop from `outer` ignores it (block)."""
        monkeypatch.setenv("FAKE_KEY", "x")
        outer = _gated(
            name="outer",
            model="fake:model",
            on={"violation": {"action": "escalate", "rule": "target"}},
        )
        del outer["unsure"]
        _write(tmp_path, outer)
        _write(tmp_path, _gated(name="target", unsure={"below": 0.9, "action": "warn"}))
        decide_fn = _ScriptedDecide({"outer": ("violation", 0.1), "target": ("violation", 0.1)})

        result, rows = _run(tmp_path, decide_fn)

        assert decide_fn.calls.count("target") == 1
        assert "deny" in str(result["stdout"])
        outer_row = _row(rows, "outer")
        assert outer_row["action"] == "block"
        assert outer_row["unsure"] is False
        target_row = _row(rows, "target")
        assert target_row["action"] == "warn"
        assert target_row["unsure"] is True
        assert target_row["unsure_below"] == 0.9
