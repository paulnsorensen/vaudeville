"""Tests for vaudeville/eval_cli.py — arg parsing and main() dispatch."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vaudeville.rules import DecideRule, DecideTestCase, parse_rule
from vaudeville.server.user_config import ProviderConfig, UserConfig

_RULE_DICT = {
    "type": "decide",
    "name": "git-gate",
    "event": "Stop",
    "model": "anthropic:claude-haiku-4-5",
    "prompt": "Classify the transcript.",
    "outcomes": ["violation", "clean"],
    "on": {"violation": "block"},
}


def _rule() -> DecideRule:
    rule = parse_rule(_RULE_DICT)
    assert isinstance(rule, DecideRule)
    return rule


class TestCrossValidateRejected:
    def test_cross_validate_rejected(self) -> None:
        with patch("sys.argv", ["eval", "--cross-validate"]):
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2


class TestCalibrateJsonRejected:
    def test_calibrate_with_json_is_a_usage_error(self) -> None:
        with patch("sys.argv", ["eval", "--calibrate", "--json", "--rule", "git-gate"]):
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2


class TestMain:
    def test_no_model_configured_prints_notice_and_exits_0(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rule = _rule()
        with (
            patch("sys.argv", ["eval"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={"git-gate": [DecideTestCase(text="t", outcome="clean")]},
            ),
            patch("vaudeville.eval_cli.load_user_config", return_value=UserConfig()),
        ):
            mock_layered.return_value.by_name.return_value = {"git-gate": rule}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        err = capsys.readouterr().err
        assert "no model configured" in err

    def test_exits_1_when_no_suite_for_specified_rule(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch("sys.argv", ["eval", "--rule", "nonexistent-rule"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
        ):
            mock_layered.return_value.by_name.return_value = {}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "No test suite found for rule: nonexistent-rule" in captured.err

    def test_filters_to_single_rule_when_specified(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rule = _rule()
        with (
            patch("sys.argv", ["eval", "--rule", "git-gate"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={
                    "git-gate": [DecideTestCase(text="t", outcome="clean")],
                    "other-rule": [DecideTestCase(text="t2", outcome="clean")],
                },
            ),
            patch("vaudeville.eval_cli.load_user_config", return_value=UserConfig()),
        ):
            mock_layered.return_value.by_name.return_value = {"git-gate": rule}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit):
                main()
        out = capsys.readouterr().out
        assert "git-gate" in out
        assert "other-rule" not in out

    def test_rules_dir_skips_layered_resolution(self, tmp_path: pathlib.Path) -> None:
        import yaml

        rule_path = tmp_path / "git-gate.yaml"
        rule_path.write_text(yaml.safe_dump(_RULE_DICT))
        mock_layered = patch(
            "vaudeville.eval_cli.load_rules_layered",
            side_effect=AssertionError("should not be called"),
        )
        with (
            patch("sys.argv", ["eval", "--rules-dir", str(tmp_path)]),
            mock_layered,
            patch("vaudeville.eval_cli.load_user_config", return_value=UserConfig()),
        ):
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

    def test_bundled_rules_load_for_eval_when_daemon_layers_are_bare(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`just eval` still loads the bundled examples layer directly,
        even though the daemon no longer includes it."""
        import yaml

        examples_dir = tmp_path / "plugin" / "examples" / "rules"
        examples_dir.mkdir(parents=True)
        (examples_dir / "git-gate.yaml").write_text(yaml.safe_dump(_RULE_DICT))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin"))

        captured: dict[str, object] = {}

        def _capture_test_cases(rules: object) -> dict[str, object]:
            captured["rules"] = rules
            return {}

        with (
            patch("sys.argv", ["eval"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch("vaudeville.eval_cli.load_test_cases", side_effect=_capture_test_cases),
        ):
            mock_layered.return_value.by_name.return_value = {}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit):
                main()

        assert "git-gate" in captured["rules"]  # type: ignore[operator]

    def test_calibrate_without_rule_is_a_usage_error(self) -> None:
        with (
            patch("sys.argv", ["eval", "--calibrate"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
        ):
            mock_layered.return_value.by_name.return_value = {}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2

    def test_calibrate_flag_prints_report_for_rule(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-10 through the flag form: `--calibrate --rule R`."""
        from vaudeville.server.agents.decide import DecideResult

        rule = _rule()
        cases = [
            DecideTestCase(text="violation case", outcome="violation"),
            DecideTestCase(text="clean case", outcome="clean"),
        ]

        def fake_decide(
            rule: DecideRule, config: UserConfig, text: str, *, model_override: object = None
        ) -> DecideResult:
            del rule, config, model_override
            # "violation case" is mispredicted so the suite has mixed correctness.
            outcome = "clean"
            return DecideResult(outcome=outcome, confidence=0.8)

        with (
            patch("sys.argv", ["eval", "--calibrate", "--rule", "git-gate"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch("vaudeville.eval_cli.load_test_cases", return_value={"git-gate": cases}),
            patch("vaudeville.eval_cli.load_user_config", return_value=UserConfig()),
            patch("vaudeville.eval.decide", side_effect=fake_decide),
        ):
            mock_layered.return_value.by_name.return_value = {"git-gate": rule}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Calibration: git-gate" in out
        assert "low-sample" in out

    def test_calibrate_full_sample_reports_all_metrics(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-10 full-sample branch (>= 30 cases) through `--calibrate --rule R`."""
        from vaudeville.server.agents.decide import DecideResult

        rule = _rule()
        cases = [DecideTestCase(text=f"match-{i}", outcome="clean") for i in range(20)]
        cases += [DecideTestCase(text=f"miss-{i}", outcome="clean") for i in range(20)]

        def fake_decide(
            rule: DecideRule, config: UserConfig, text: str, *, model_override: object = None
        ) -> DecideResult:
            del rule, config, model_override
            if text.startswith("match"):
                return DecideResult(outcome="clean", confidence=0.9)
            return DecideResult(outcome="violation", confidence=0.3)

        with (
            patch("sys.argv", ["eval", "--calibrate", "--rule", "git-gate"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch("vaudeville.eval_cli.load_test_cases", return_value={"git-gate": cases}),
            patch("vaudeville.eval_cli.load_user_config", return_value=UserConfig()),
            patch("vaudeville.eval.decide", side_effect=fake_decide),
        ):
            mock_layered.return_value.by_name.return_value = {"git-gate": rule}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Calibration: git-gate" in out
        assert "low-sample" not in out
        assert "ROC AUC: 1.000" in out
        assert "KS:      1.000" in out
        assert "Brier:   0.0500" in out
        assert "ECE:     0.2000" in out
        assert "Threshold sweep:" in out
        assert "Recommended unsure.below=0.35" in out

    def test_calibrate_no_confidence_reports_na_never_a_deferral_notice(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-10 no-confidence branch: n/a, and no recommendation is ever printed."""
        from vaudeville.server.agents.decide import DecideResult

        rule = _rule()
        cases = [
            DecideTestCase(text="violation case", outcome="violation"),
            DecideTestCase(text="clean case", outcome="clean"),
        ]

        def fake_decide(
            rule: DecideRule, config: UserConfig, text: str, *, model_override: object = None
        ) -> DecideResult:
            del rule, config, model_override
            outcome = "violation" if "violation" in text else "clean"
            return DecideResult(outcome=outcome, confidence=None)

        with (
            patch("sys.argv", ["eval", "--calibrate", "--rule", "git-gate"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch("vaudeville.eval_cli.load_test_cases", return_value={"git-gate": cases}),
            patch("vaudeville.eval_cli.load_user_config", return_value=UserConfig()),
            patch("vaudeville.eval.decide", side_effect=fake_decide),
        ):
            mock_layered.return_value.by_name.return_value = {"git-gate": rule}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "calibration is n/a" in out
        assert "Recommended" not in out
        assert "No threshold reached" not in out


class TestJsonRunSummary:
    def test_json_run_summary_has_expected_shape(self, capsys: pytest.CaptureFixture[str]) -> None:
        from vaudeville.server.agents.decide import DecideResult

        rule = _rule()
        cases = [
            DecideTestCase(text="violation case", outcome="violation"),
            DecideTestCase(text="clean case", outcome="clean"),
        ]

        def fake_decide(
            rule: DecideRule, config: UserConfig, text: str, *, model_override: object = None
        ) -> DecideResult:
            del rule, config, model_override
            outcome = "violation" if "violation" in text else "clean"
            return DecideResult(outcome=outcome, confidence=0.8)

        with (
            patch("sys.argv", ["eval", "--json", "--rule", "git-gate"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch("vaudeville.eval_cli.load_test_cases", return_value={"git-gate": cases}),
            patch("vaudeville.eval_cli.load_user_config", return_value=UserConfig()),
            patch("vaudeville.eval.decide", side_effect=fake_decide),
        ):
            mock_layered.return_value.by_name.return_value = {"git-gate": rule}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["passed"] is True
        rule_summary = record["rules"][0]
        assert rule_summary["rule"] == "git-gate"
        assert rule_summary["n"] == 2
        assert rule_summary["tp"] == 1
        assert rule_summary["fp"] == 0
        assert rule_summary["tn"] == 1
        assert rule_summary["fn"] == 0
        assert rule_summary["precision"] == 1.0
        assert rule_summary["recall"] == 1.0
        assert rule_summary["f1"] == 1.0
        assert rule_summary["passed"] is True
        assert rule_summary["calibration"]["status"] == "low-sample"

    def test_json_n_counts_every_case_including_non_positive_mislabel(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A `ticket` vs `clean` mislabel is in no tp/fp/tn/fn bucket; `n`
        still counts it and agrees with `calibration.n`."""
        from vaudeville.server.agents.decide import DecideResult

        rule = parse_rule({**_RULE_DICT, "outcomes": ["violation", "ticket", "clean"]})
        assert isinstance(rule, DecideRule)
        cases = [
            DecideTestCase(text="violation case", outcome="violation"),
            DecideTestCase(text="clean case", outcome="clean"),
            DecideTestCase(text="ticket case", outcome="ticket"),
        ]

        def fake_decide(
            rule: DecideRule, config: UserConfig, text: str, *, model_override: object = None
        ) -> DecideResult:
            del rule, config, model_override
            # "ticket case" is mislabeled as clean: both outcomes are non-positive.
            outcome = "violation" if "violation" in text else "clean"
            return DecideResult(outcome=outcome, confidence=0.8)

        with (
            patch("sys.argv", ["eval", "--json", "--rule", "git-gate"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch("vaudeville.eval_cli.load_test_cases", return_value={"git-gate": cases}),
            patch("vaudeville.eval_cli.load_user_config", return_value=UserConfig()),
            patch("vaudeville.eval.decide", side_effect=fake_decide),
        ):
            mock_layered.return_value.by_name.return_value = {"git-gate": rule}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit):
                main()
        record = json.loads(capsys.readouterr().out)
        rule_summary = record["rules"][0]
        assert (
            rule_summary["tp"],
            rule_summary["fp"],
            rule_summary["tn"],
            rule_summary["fn"],
        ) == (
            1,
            0,
            1,
            0,
        )
        assert rule_summary["n"] == 3
        assert rule_summary["calibration"]["n"] == 3
        assert rule_summary["precision"] == 1.0
        assert rule_summary["recall"] == 1.0

    def test_json_run_summary_prints_one_object_with_no_model_configured(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rule = _rule()
        with (
            patch("sys.argv", ["eval", "--json"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={"git-gate": [DecideTestCase(text="t", outcome="clean")]},
            ),
            patch("vaudeville.eval_cli.load_user_config", return_value=UserConfig()),
        ):
            mock_layered.return_value.by_name.return_value = {"git-gate": rule}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert "passed" in record
        assert record["rules"][0]["calibration"]["status"] == "n/a"

    def test_json_with_no_suite_for_rule_stays_out_of_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The no-suite message must not corrupt the `--json` stdout contract."""
        with (
            patch("sys.argv", ["eval", "--json", "--rule", "nonexistent-rule"]),
            patch("vaudeville.eval_cli.load_rules_layered") as mock_layered,
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
        ):
            mock_layered.return_value.by_name.return_value = {}
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "No test suite found for rule: nonexistent-rule" in captured.err


class TestMainEndToEnd:
    def test_main_prints_tp_fp_tn_fn_for_decide_rule(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import yaml

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
        rule_dict: dict[str, object] = {
            **_RULE_DICT,
            "test_cases": [
                {"text": "violation case", "outcome": "violation"},
                {"text": "clean case", "outcome": "clean"},
            ],
        }
        rule_path = tmp_path / "git-gate.yaml"
        rule_path.write_text(yaml.safe_dump(rule_dict))

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            del messages, info
            return ModelResponse(parts=[TextPart('{"outcome": "violation"}')])

        model = FunctionModel(respond)

        with (
            patch("sys.argv", ["eval", "--rules-dir", str(tmp_path)]),
            patch(
                "vaudeville.eval_cli.load_user_config",
                return_value=UserConfig(
                    providers={"anthropic": ProviderConfig(key_env="ANTHROPIC_API_KEY")}
                ),
            ),
        ):
            from vaudeville.eval_cli import main

            with pytest.raises(SystemExit):
                main(model_override=model)

        out = capsys.readouterr().out
        assert "Confusion: TP=1 FP=1 TN=0 FN=0" in out
