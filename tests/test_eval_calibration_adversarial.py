"""Adversarial contract tests for the eval run summary, calibration, and
the decide confidence source (AC-2, AC-3, AC-4, AC-10).

Attack vectors: a multi-rule `--json` run with one failing rule, the real
`--json` producer piped into the orchestrator's `_eval_rule` consumer,
the 30-case sample boundary, a single-class confident set (undefined ROC
AUC and KS), sweep equality semantics against the gate's strict `<`,
None confidences mixed with scored ones, and falsy or invalid
`provider_details` values.
"""

from __future__ import annotations

import json
import math
import subprocess
from typing import Any
from unittest.mock import patch

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vaudeville.eval import CaseResult
from vaudeville.eval_calibrate import calibrate, format_calibration
from vaudeville.orchestrator import Thresholds
from vaudeville.rules import DecideRule, DecideTestCase, parse_rule
from vaudeville.server.agents.decide import DecideResult, decide
from vaudeville.server.user_config import ProviderConfig, UserConfig


def _rule(name: str) -> DecideRule:
    rule = parse_rule(
        {
            "type": "decide",
            "name": name,
            "event": "Stop",
            "model": "anthropic:claude-haiku-4-5",
            "prompt": "Classify.",
            "outcomes": ["violation", "clean"],
            "on": {"violation": "block"},
        }
    )
    assert isinstance(rule, DecideRule)
    return rule


def _case(i: int, confidence: float | None, *, correct: bool) -> CaseResult:
    return CaseResult(
        rule="r",
        case_id=i,
        text="t",
        expected="clean",
        predicted="clean" if correct else "violation",
        confidence=confidence,
    )


def _run_main(
    argv: list[str],
    rules: dict[str, DecideRule],
    suites: dict[str, list[DecideTestCase]],
    fake_decide: Any,
) -> int:
    with (
        patch("sys.argv", ["eval", *argv]),
        patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
        patch("vaudeville.eval_cli.load_test_cases", return_value=suites),
        patch("vaudeville.eval_cli.load_user_config", return_value=UserConfig()),
        patch("vaudeville.eval.decide", side_effect=fake_decide),
    ):
        mock_layered.return_value.by_name.return_value = rules
        from vaudeville.eval_cli import main

        with pytest.raises(SystemExit) as exc_info:
            main()
    code = exc_info.value.code
    assert isinstance(code, int)
    return code


def _text_decide(confidence: float | None) -> Any:
    """Predicts the outcome named in the case text: `good-*` cases are
    right, `bad-*` cases are the wrong outcome."""

    def fake_decide(
        rule: DecideRule, config: UserConfig, text: str, *, model_override: object = None
    ) -> DecideResult:
        del rule, config, model_override
        expected = "violation" if "violation" in text else "clean"
        other = "clean" if expected == "violation" else "violation"
        return DecideResult(
            outcome=expected if text.startswith("good") else other, confidence=confidence
        )

    return fake_decide


_TWO_RULE_SUITES = {
    "alpha-rule": [
        DecideTestCase(text="good violation", outcome="violation"),
        DecideTestCase(text="good clean", outcome="clean"),
    ],
    "beta-rule": [
        DecideTestCase(text="good violation", outcome="violation"),
        DecideTestCase(text="bad violation", outcome="violation"),
        DecideTestCase(text="bad clean", outcome="clean"),
        DecideTestCase(text="good clean", outcome="clean"),
    ],
}


def _two_rule_json(capsys: pytest.CaptureFixture[str]) -> str:
    """Run `--json` over two rules. `UserConfig()` has no model, so the
    exit code skips the pass/fail gate; the summary still reports it."""
    rules = {"alpha-rule": _rule("alpha-rule"), "beta-rule": _rule("beta-rule")}
    _run_main(["--json"], rules, _TWO_RULE_SUITES, _text_decide(0.8))
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# AC-2: one JSON object for a multi-rule run
# ---------------------------------------------------------------------------


class TestRunSummaryMultiRule:
    def test_failing_rule_flips_top_level_passed_in_one_object(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = _two_rule_json(capsys)

        record = json.loads(out)  # the whole stdout is exactly one object
        assert record["passed"] is False
        by_rule = {r["rule"]: r for r in record["rules"]}
        assert set(by_rule) == {"alpha-rule", "beta-rule"}
        assert by_rule["alpha-rule"]["passed"] is True
        beta = by_rule["beta-rule"]
        assert beta["passed"] is False
        assert (beta["tp"], beta["fp"], beta["tn"], beta["fn"]) == (1, 1, 1, 1)
        assert beta["n"] == 4
        assert beta["precision"] == 0.5
        assert beta["recall"] == 0.5
        assert beta["f1"] == 0.5
        for entry in record["rules"]:
            assert set(entry) >= {
                "rule", "n", "tp", "fp", "tn", "fn",
                "precision", "recall", "f1", "passed", "calibration",
            }  # fmt: skip
            assert entry["calibration"]["status"] == "low-sample"


# ---------------------------------------------------------------------------
# AC-3: the real `--json` producer feeds the orchestrator consumer
# ---------------------------------------------------------------------------


class TestEvalRuleConsumesRealSummary:
    @pytest.mark.parametrize(
        ("rule_name", "expected"),
        [
            ("alpha-rule", Thresholds(p_min=1.0, r_min=1.0, f1_min=1.0)),
            ("beta-rule", Thresholds(p_min=0.5, r_min=0.5, f1_min=0.5)),
        ],
    )
    def test_eval_rule_parses_producer_output_for_the_named_rule(
        self, capsys: pytest.CaptureFixture[str], rule_name: str, expected: Thresholds
    ) -> None:
        """A field rename on either side breaks this round trip; a consumer
        that takes the first entry fails the `beta-rule` row."""
        stdout = _two_rule_json(capsys)
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout, stderr="")

        from vaudeville.orchestrator._abandon import _eval_rule

        with patch("subprocess.run", return_value=completed):
            assert _eval_rule(rule_name, "/proj") == expected


# ---------------------------------------------------------------------------
# AC-10: calibration boundaries
# ---------------------------------------------------------------------------


class TestCalibrationBoundaries:
    @pytest.mark.parametrize(("n", "status"), [(29, "low-sample"), (30, "ok")])
    def test_thirty_case_boundary(self, n: int, status: str) -> None:
        cases = [_case(i, 0.9, correct=i % 2 == 0) for i in range(n)]
        assert calibrate(cases).status == status

    def test_single_class_set_reports_na_auc_and_ks_without_raising(self) -> None:
        """A rule that gets every case right has no negatives: ROC AUC and
        KS are undefined, but Brier, ECE, and the sweep still hold."""
        cases = [_case(i, 0.9, correct=True) for i in range(5)]

        result = calibrate(cases)

        assert result.roc_auc is None
        assert result.ks is None
        assert result.brier == pytest.approx(0.01)
        assert result.ece == pytest.approx(0.1)
        assert result.recommended_below == 0.05
        assert result.unsure_rate == 0.0
        text = format_calibration("r", result)
        assert "ROC AUC: n/a" in text
        assert "KS:      n/a" in text

    def test_single_class_through_cli_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        suites = {"alpha-rule": _TWO_RULE_SUITES["alpha-rule"]}
        code = _run_main(
            ["--calibrate", "--rule", "alpha-rule"],
            {"alpha-rule": _rule("alpha-rule")},
            suites,
            _text_decide(0.8),
        )

        assert code == 0
        out = capsys.readouterr().out
        assert "Calibration: alpha-rule" in out
        assert "ROC AUC: n/a" in out

    def test_sweep_counts_confidence_equal_to_threshold_as_confident(self) -> None:
        """The gate fires on `confidence < below`, so a case at exactly
        `below` is not unsure. The sweep must agree: at 0.50 the ten
        correct 0.50 cases are confident and the one wrong 0.45 case is not."""
        cases = [_case(0, 0.45, correct=False)]
        cases += [_case(i, 0.5, correct=True) for i in range(1, 11)]

        result = calibrate(cases)

        assert result.recommended_below == 0.5
        assert result.unsure_rate == pytest.approx(1 / 11)
        point = next(p for p in result.sweep if p.threshold == 0.5)
        assert point.precision == 1.0

    def test_none_confidences_are_excluded_not_scored_as_zero(self) -> None:
        """`n` counts every case; the metrics use only confident cases."""
        scored = [
            _case(0, 0.9, correct=True),
            _case(1, 0.8, correct=True),
            _case(2, 0.2, correct=False),
            _case(3, 0.1, correct=False),
        ]
        unscored = [_case(4, None, correct=True), _case(5, None, correct=False)]

        result = calibrate(scored + unscored)

        assert result.n == 6
        assert result.roc_auc == 1.0
        assert result.ks == 1.0
        assert result.brier == pytest.approx(0.025)
        assert result.ece == pytest.approx(0.15)
        assert result.recommended_below == 0.25
        assert result.unsure_rate == 0.5


# ---------------------------------------------------------------------------
# AC-4: provider_details edges
# ---------------------------------------------------------------------------


class TestProviderDetailsEdges:
    def _decide(self, monkeypatch: pytest.MonkeyPatch, provider_details: Any) -> float | None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
        config = UserConfig(providers={"anthropic": ProviderConfig(key_env="ANTHROPIC_API_KEY")})

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            del messages, info
            return ModelResponse(
                parts=[TextPart('{"outcome": "violation"}')],
                provider_details=provider_details,
            )

        result = decide(_rule("r"), config, "text", model_override=FunctionModel(respond))
        return result.confidence

    def test_zero_probability_is_kept_not_replaced_by_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        confidence = self._decide(
            monkeypatch,
            {"probabilities": {"outcome": {"violation": 0.0}}, "confidence": {"outcome": 0.9}},
        )
        assert confidence == 0.0

    def test_chosen_outcome_probability_is_read_not_the_max(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        confidence = self._decide(
            monkeypatch, {"probabilities": {"outcome": {"violation": 0.3, "clean": 0.7}}}
        )
        assert confidence == 0.3

    @pytest.mark.parametrize("bad", [math.nan, 1.5, -0.1, "0.8", True])
    def test_invalid_probability_falls_back_to_confidence(
        self, monkeypatch: pytest.MonkeyPatch, bad: object
    ) -> None:
        confidence = self._decide(
            monkeypatch,
            {"probabilities": {"outcome": {"violation": bad}}, "confidence": {"outcome": 0.4}},
        )
        assert confidence == 0.4

    def test_integer_probability_is_returned_as_float(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        confidence = self._decide(monkeypatch, {"probabilities": {"outcome": {"violation": 1}}})
        assert confidence == 1.0
        assert isinstance(confidence, float)
