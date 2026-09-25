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

    def test_exits_1_when_no_suite_for_specified_rule(self) -> None:
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
            outcome = "violation" if "violation" in text else "clean"
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
        for key in (
            "rule",
            "n",
            "tp",
            "fp",
            "tn",
            "fn",
            "precision",
            "recall",
            "f1",
            "passed",
            "calibration",
        ):
            assert key in rule_summary

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
