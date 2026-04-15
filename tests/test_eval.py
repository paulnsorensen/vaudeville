"""Tests for vaudeville/eval.py — eval harness."""

from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from conftest import MockBackend
from vaudeville.core.rules import Rule
from vaudeville.eval import (
    EvalCase,
    EvalResults,
    classify_case,
    _find_project_root,
    evaluate_rule,
    load_test_cases,
)
from vaudeville.eval_report import (
    MIN_CALIBRATION_CASES,
    _find_best_threshold,
    _git_head,
    CalibrateTarget,
    calibrate_rule,
    cross_validate_rule,
    find_rule_file,
    print_results,
    run_evaluations,
    write_eval_log,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def rules() -> dict[str, Rule]:
    return {
        "violation-detector": Rule(
            name="violation-detector",
            event="Stop",
            prompt="Classify:\n{text}\nVERDICT:",
            context=[{"field": "last_assistant_message"}],
            action="block",
            message="{reason}",
            threshold=0.0,
        ),
    }


@pytest.fixture
def two_cases() -> list[EvalCase]:
    return [
        EvalCase(text="This should work fine.", label="violation"),
        EvalCase(text="All tests pass with no issues.", label="clean"),
    ]


class TestFindProjectRoot:
    def test_returns_path_in_git_repo(self) -> None:
        result = _find_project_root()
        assert result is not None
        assert os.path.isdir(result)

    def test_returns_none_on_oserror(self) -> None:
        with patch("subprocess.run", side_effect=OSError):
            assert _find_project_root() is None

    def test_returns_none_on_timeout(self) -> None:
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 5),
        ):
            assert _find_project_root() is None

    def test_returns_none_on_nonzero_returncode(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 128
            mock_run.return_value.stdout = ""
            assert _find_project_root() is None


class TestLoadTestCases:
    def _rule(self, name: str, test_cases: list[EvalCase]) -> Rule:
        return Rule(
            name=name,
            event="Stop",
            prompt="Classify:\n{text}\nVERDICT:",
            context=[{"field": "last_assistant_message"}],
            action="block",
            message="{reason}",
            test_cases=test_cases,
        )

    def test_empty_rules_dict_returns_empty(self) -> None:
        assert load_test_cases({}) == {}

    def test_rules_without_test_cases_are_omitted(self) -> None:
        rules = {"r": self._rule("r", [])}
        assert load_test_cases(rules) == {}

    def test_returns_cases_keyed_by_rule_name(self) -> None:
        cases = [EvalCase(text="example text", label="clean")]
        rules = {"test-rule": self._rule("test-rule", cases)}
        result = load_test_cases(rules)
        assert "test-rule" in result
        assert len(result["test-rule"]) == 1
        assert result["test-rule"][0].text == "example text"

    def test_mixed_rules_only_returns_populated(self) -> None:
        cases = [EvalCase(text="t", label="clean")]
        rules = {
            "with-cases": self._rule("with-cases", cases),
            "empty-cases": self._rule("empty-cases", []),
        }
        result = load_test_cases(rules)
        assert set(result.keys()) == {"with-cases"}


class TestCondenseIntegration:
    """Verify condense_text is called for Stop+long, skipped otherwise."""

    def test_condense_called_for_stop_long_text(self, rules: dict[str, Rule]) -> None:
        long_text = "x" * 250
        backend = MockBackend(verdict="clean", reason="ok")
        results = EvalResults(rule="violation-detector")
        case = EvalCase(text=long_text, label="clean")
        with patch("vaudeville.eval.condense_text", return_value="condensed") as mock:
            classify_case(case, rules["violation-detector"], backend, results)
        mock.assert_called_once_with(long_text, backend)

    def test_condense_skipped_for_stop_short_text(self, rules: dict[str, Rule]) -> None:
        short_text = "short text"
        backend = MockBackend(verdict="clean", reason="ok")
        results = EvalResults(rule="violation-detector")
        case = EvalCase(text=short_text, label="clean")
        with patch("vaudeville.eval.condense_text") as mock:
            classify_case(case, rules["violation-detector"], backend, results)
        mock.assert_not_called()

    def test_condense_skipped_for_pretooluse(self) -> None:
        rule = Rule(
            name="tool-rule",
            event="PreToolUse",
            prompt="Classify:\n{text}\nVERDICT:",
            context=[],
            action="block",
            message="{reason}",
            threshold=0.0,
        )
        long_text = "x" * 250
        backend = MockBackend(verdict="clean", reason="ok")
        results = EvalResults(rule="tool-rule")
        case = EvalCase(text=long_text, label="clean")
        with patch("vaudeville.eval.condense_text") as mock:
            classify_case(case, rule, backend, results)
        mock.assert_not_called()


class TestClassifyCase:
    def test_true_positive(self, rules: dict[str, Rule]) -> None:
        backend = MockBackend(verdict="violation", reason="found issue")
        results = EvalResults(rule="violation-detector")
        case = EvalCase(text="this should work", label="violation")
        classify_case(case, rules["violation-detector"], backend, results)
        assert results.tp == 1
        assert results.fp == 0

    def test_true_negative(self, rules: dict[str, Rule]) -> None:
        backend = MockBackend(verdict="clean", reason="ok")
        results = EvalResults(rule="violation-detector")
        case = EvalCase(text="all good", label="clean")
        classify_case(case, rules["violation-detector"], backend, results)
        assert results.tn == 1

    def test_false_positive(self, rules: dict[str, Rule]) -> None:
        backend = MockBackend(verdict="violation", reason="false alarm")
        results = EvalResults(rule="violation-detector")
        case = EvalCase(text="all tests pass", label="clean")
        classify_case(case, rules["violation-detector"], backend, results)
        assert results.fp == 1
        assert len(results.misclassified) == 1
        assert results.misclassified[0]["actual"] == "clean"

    def test_false_negative(self, rules: dict[str, Rule]) -> None:
        backend = MockBackend(verdict="clean", reason="missed it")
        results = EvalResults(rule="violation-detector")
        case = EvalCase(text="this might work", label="violation")
        classify_case(case, rules["violation-detector"], backend, results)
        assert results.fn == 1
        assert results.misclassified[0]["actual"] == "violation"

    def test_threshold_downgrades_low_confidence_violation(self) -> None:
        """A violation with confidence below the rule threshold becomes clean."""
        rule = Rule(
            name="test-rule",
            event="Stop",
            prompt="Classify:\n{text}\nVERDICT:",
            context=[],
            action="block",
            message="{reason}",
            threshold=0.7,
        )
        backend = MockBackend(
            verdict="violation",
            reason="found issue",
            logprobs={"violation": -1.5, "clean": -0.5},
        )
        results = EvalResults(rule="test-rule")
        case = EvalCase(text="borderline text", label="clean")
        cr = classify_case(case, rule, backend, results)
        assert cr.predicted == "clean"
        assert results.tn == 1
        assert results.fp == 0

    def test_threshold_keeps_high_confidence_violation(self) -> None:
        """A violation with confidence above the rule threshold stays violation."""
        rule = Rule(
            name="test-rule",
            event="Stop",
            prompt="Classify:\n{text}\nVERDICT:",
            context=[],
            action="block",
            message="{reason}",
            threshold=0.5,
        )
        backend = MockBackend(
            verdict="violation",
            reason="found issue",
            logprobs={"violation": -0.05, "clean": -5.0},
        )
        results = EvalResults(rule="test-rule")
        case = EvalCase(text="hedging text", label="violation")
        cr = classify_case(case, rule, backend, results)
        assert cr.predicted == "violation"
        assert results.tp == 1

    def test_threshold_zero_disables_downgrade(self) -> None:
        """threshold=0.0 means no downgrading even with zero confidence."""
        rule = Rule(
            name="test-rule",
            event="Stop",
            prompt="Classify:\n{text}\nVERDICT:",
            context=[],
            action="block",
            message="{reason}",
            threshold=0.0,
        )
        backend = MockBackend(verdict="violation", reason="found issue")
        results = EvalResults(rule="test-rule")
        case = EvalCase(text="test text", label="violation")
        classify_case(case, rule, backend, results)
        assert results.tp == 1


class TestEvaluateRule:
    def test_raises_for_unknown_rule(self, rules: dict[str, Rule]) -> None:
        backend = MockBackend()
        with pytest.raises(ValueError, match="Rule not found"):
            evaluate_rule("unknown-rule", [], rules, backend)

    def test_aggregates_results(
        self, rules: dict[str, Rule], two_cases: list[EvalCase]
    ) -> None:
        backend = MockBackend(verdict="violation")
        results, _ = evaluate_rule("violation-detector", two_cases, rules, backend)
        assert results.total == 2


class TestCrossValidateRule:
    def test_raises_for_unknown_rule(self, rules: dict[str, Rule]) -> None:
        with pytest.raises(ValueError, match="Rule not found"):
            cross_validate_rule("unknown", [], rules, MockBackend())

    def test_produces_correct_totals(
        self,
        rules: dict[str, Rule],
        two_cases: list[EvalCase],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        backend = MockBackend(verdict="violation")
        results = cross_validate_rule("violation-detector", two_cases, rules, backend)
        assert results.total == 2
        out = capsys.readouterr().out
        assert "Fold" in out

    def test_tracks_misclassifications(self, rules: dict[str, Rule]) -> None:
        cases = [EvalCase(text="good text", label="clean")]
        backend = MockBackend(verdict="violation")  # FP
        results = cross_validate_rule("violation-detector", cases, rules, backend)
        assert results.fp == 1
        assert len(results.misclassified) == 1


class TestPrintResults:
    def test_pass_when_precision_and_recall_met(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        results = EvalResults(rule="test", tp=19, fp=1, tn=0, fn=4)
        passed = print_results(results)
        assert passed is True
        out = capsys.readouterr().out
        assert "PASS" in out

    def test_fail_when_below_threshold(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        results = EvalResults(rule="test", tp=5, fp=5, tn=0, fn=5)
        passed = print_results(results)
        assert passed is False
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_prints_misclassifications(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        results = EvalResults(
            rule="test",
            tp=0,
            fp=1,
            tn=0,
            fn=0,
            misclassified=[
                {"text": "bad text", "actual": "clean", "predicted": "violation"}
            ],
        )
        print_results(results)
        out = capsys.readouterr().out
        assert "Misclassifications" in out
        assert "bad text" in out


class TestThresholdSweep:
    def test_prints_sweep_table(
        self,
        rules: dict[str, Rule],
        two_cases: list[EvalCase],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from vaudeville.eval_report import threshold_sweep

        backend = MockBackend(verdict="violation", reason="found issue")
        suites = {"violation-detector": two_cases}
        threshold_sweep(suites, rules, backend)
        out = capsys.readouterr().out
        assert "Threshold sweep: violation-detector" in out
        assert "Thresh" in out

    def test_skips_unknown_rules(
        self,
        rules: dict[str, Rule],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from vaudeville.eval_report import threshold_sweep

        suites = {"nonexistent-rule": [EvalCase("text", "clean")]}
        threshold_sweep(suites, rules, MockBackend())
        out = capsys.readouterr().out
        assert "Threshold sweep" not in out

    def test_sweep_all_thresholds_printed(
        self,
        rules: dict[str, Rule],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from vaudeville.eval_report import threshold_sweep

        cases = [
            EvalCase(text="hedging text", label="violation"),
            EvalCase(text="clean text here", label="clean"),
        ]
        backend = MockBackend(verdict="violation", reason="found issue")
        suites = {"violation-detector": cases}
        threshold_sweep(suites, rules, backend)
        out = capsys.readouterr().out
        assert "0.30" in out
        assert "0.90" in out


class TestRunInference:
    def test_non_logprob_backend_uses_classify(self) -> None:
        """A backend without classify_with_logprobs falls back to classify()."""
        from vaudeville.eval import _run_inference

        class PlainBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                return "VERDICT: clean\nREASON: ok"

        backend = PlainBackend()
        result = _run_inference(backend, "test prompt")
        assert result.text == "VERDICT: clean\nREASON: ok"
        assert result.logprobs == {}


class TestBuildBackend:
    def test_returns_mlx_backend(self) -> None:
        import argparse

        from vaudeville.eval import _build_backend

        args = argparse.Namespace(model="test-model")
        mock_instance = MagicMock()
        mock_mlx = MagicMock(return_value=mock_instance)
        with patch("vaudeville.server.MLXBackend", mock_mlx):
            backend = _build_backend(args)
        mock_mlx.assert_called_once_with("test-model")
        assert backend is mock_instance


class TestRunEvaluations:
    def test_skips_rule_with_no_definition(
        self, rules: dict[str, Rule], capsys: pytest.CaptureFixture[str]
    ) -> None:
        import argparse

        args = argparse.Namespace(cross_validate=False)
        test_suites = {"undefined-rule": [EvalCase("text", "clean")]}
        passed, all_results = run_evaluations(args, rules, test_suites, MockBackend())
        assert passed is True
        assert all_results == {}
        out = capsys.readouterr().out
        assert "WARNING" in out

    def test_fails_when_no_positive_cases(self, rules: dict[str, Rule]) -> None:
        import argparse

        args = argparse.Namespace(cross_validate=False)
        cases = [EvalCase("text", "clean")]
        backend = MockBackend(verdict="clean")
        test_suites = {"violation-detector": cases}
        passed, all_results = run_evaluations(args, rules, test_suites, backend)
        assert passed is False
        assert "violation-detector" in all_results

    def test_cross_validate_flag_routes_to_cross_validate(
        self, rules: dict[str, Rule]
    ) -> None:
        import argparse

        args = argparse.Namespace(cross_validate=True)
        cases = [EvalCase("text", "clean")]
        backend = MockBackend(verdict="clean")
        test_suites = {"violation-detector": cases}
        with patch("vaudeville.eval_report.cross_validate_rule") as mock_cv:
            mock_cv.return_value = EvalResults(rule="violation-detector", tp=1)
            run_evaluations(args, rules, test_suites, backend)
        mock_cv.assert_called_once()


class TestMain:
    def test_exits_0_on_all_pass(self, capsys: pytest.CaptureFixture[str]) -> None:
        mock_backend = MockBackend(verdict="clean")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        with (
            patch("sys.argv", ["eval"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval.load_rules_layered",
                return_value={
                    "violation-detector": Rule(
                        name="violation-detector",
                        event="Stop",
                        prompt="Classify:\n{text}\nVERDICT:",
                        context=[{"field": "last_assistant_message"}],
                        action="block",
                        message="{reason}",
                        threshold=0.0,
                    ),
                },
            ),
            patch("vaudeville.eval.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval import main

                main()
        assert exc_info.value.code == 0

    def test_filters_to_single_rule_when_specified(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_backend = MockBackend(verdict="clean")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        with (
            patch("sys.argv", ["eval", "--rule", "violation-detector"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval.load_rules_layered",
                return_value={
                    "violation-detector": Rule(
                        name="violation-detector",
                        event="Stop",
                        prompt="Classify:\n{text}\nVERDICT:",
                        context=[{"field": "last_assistant_message"}],
                        action="block",
                        message="{reason}",
                        threshold=0.0,
                    ),
                },
            ),
            patch(
                "vaudeville.eval.load_test_cases",
                return_value={"violation-detector": [EvalCase("text", "clean")]},
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval import main

                main()
        assert (
            exc_info.value.code == 1
        )  # clean backend + clean case → 0 TP → fails threshold

    def test_exits_1_when_no_suite_for_specified_rule(self) -> None:
        mock_mlx_cls = MagicMock(return_value=MockBackend())
        with (
            patch("sys.argv", ["eval", "--rule", "nonexistent-rule"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval.load_rules_layered",
                return_value={
                    "violation-detector": Rule(
                        name="violation-detector",
                        event="Stop",
                        prompt="Classify:\n{text}\nVERDICT:",
                        context=[{"field": "last_assistant_message"}],
                        action="block",
                        message="{reason}",
                        threshold=0.0,
                    ),
                },
            ),
            patch("vaudeville.eval.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval import main

                main()
        assert exc_info.value.code == 1

    def test_test_file_with_wrong_rule_exits_1(self) -> None:
        mock_mlx_cls = MagicMock(return_value=MockBackend())
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.dump(
                {"rule": "other-rule", "cases": [{"text": "t", "label": "clean"}]},
                f,
            )
            tf = f.name
        with (
            patch(
                "sys.argv",
                ["eval", "--rule", "violation-detector", "--test-file", tf],
            ),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval.load_rules_layered",
                return_value={
                    "violation-detector": Rule(
                        name="violation-detector",
                        event="Stop",
                        prompt="Classify:\n{text}\nVERDICT:",
                        context=[{"field": "last_assistant_message"}],
                        action="block",
                        message="{reason}",
                        threshold=0.0,
                    ),
                },
            ),
            patch("vaudeville.eval.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval import main

                main()
        assert exc_info.value.code == 1


class TestGitHead:
    def test_returns_short_hash(self) -> None:
        result = _git_head()
        assert result != "unknown"
        assert len(result) >= 7

    def test_returns_unknown_on_oserror(self) -> None:
        with patch("vaudeville.eval_report.subprocess.run", side_effect=OSError):
            assert _git_head() == "unknown"

    def test_returns_unknown_on_timeout(self) -> None:
        with patch(
            "vaudeville.eval_report.subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 5),
        ):
            assert _git_head() == "unknown"

    def test_returns_unknown_on_nonzero_returncode(self) -> None:
        with patch("vaudeville.eval_report.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 128
            mock_run.return_value.stdout = ""
            assert _git_head() == "unknown"


class TestWriteEvalLog:
    def test_appends_jsonl_line(self) -> None:
        import json

        results = {
            "violation-detector": EvalResults(
                rule="violation-detector", tp=19, fp=1, tn=10, fn=2
            ),
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            log_path = f.name

        write_eval_log(log_path, "test-model", results)

        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["model"] == "test-model"
        assert "timestamp" in entry
        assert "git_head" in entry
        assert "violation-detector" in entry["rules"]
        rule_data = entry["rules"]["violation-detector"]
        assert rule_data["precision"] == round(19 / 20, 4)
        assert rule_data["recall"] == round(19 / 21, 4)
        assert "f1" in rule_data
        os.unlink(log_path)

    def test_appends_multiple_lines(self) -> None:
        import json

        results = {
            "test-rule": EvalResults(rule="test-rule", tp=10, fp=0, tn=10, fn=0),
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            log_path = f.name

        write_eval_log(log_path, "model-a", results)
        write_eval_log(log_path, "model-b", results)

        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["model"] == "model-a"
        assert json.loads(lines[1])["model"] == "model-b"
        os.unlink(log_path)

    def test_empty_results_writes_empty_rules(self) -> None:
        import json

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            log_path = f.name

        write_eval_log(log_path, "model", {})

        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert entry["rules"] == {}
        os.unlink(log_path)


class TestFindRuleFile:
    def test_finds_rule_in_directory(self, tmp_path: pathlib.Path) -> None:
        rule_yaml = {"name": "test-rule", "prompt": "test {text}"}
        rule_file = tmp_path / "test-rule.yaml"
        rule_file.write_text(yaml.dump(rule_yaml))
        result = find_rule_file("test-rule", [str(tmp_path)])
        assert result == str(rule_file)

    def test_returns_none_when_not_found(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "other.yaml").write_text(yaml.dump({"name": "other"}))
        result = find_rule_file("nonexistent", [str(tmp_path)])
        assert result is None

    def test_handles_missing_directory(self) -> None:
        result = find_rule_file("test", ["/nonexistent/path/does/not/exist"])
        assert result is None

    def test_skips_non_yaml_files(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "readme.txt").write_text("name: test-rule")
        result = find_rule_file("test-rule", [str(tmp_path)])
        assert result is None

    def test_searches_multiple_dirs(self, tmp_path: pathlib.Path) -> None:
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        rule_file = dir2 / "my-rule.yaml"
        rule_file.write_text(yaml.dump({"name": "my-rule", "prompt": "t"}))
        result = find_rule_file("my-rule", [str(dir1), str(dir2)])
        assert result == str(rule_file)


class TestFindBestThreshold:
    def test_finds_optimal_threshold(self) -> None:
        from vaudeville.eval import CaseResult

        case_results = (
            [
                CaseResult(
                    text="v", label="violation", predicted="violation", confidence=0.9
                )
                for _ in range(19)
            ]
            + [
                CaseResult(
                    text="fp", label="clean", predicted="violation", confidence=0.4
                )
                for _ in range(2)
            ]
            + [
                CaseResult(text="c", label="clean", predicted="clean", confidence=0.0)
                for _ in range(5)
            ]
        )
        thresh, f1 = _find_best_threshold("test-rule", case_results)
        assert thresh == 0.45
        assert f1 > 0.0

    def test_returns_zero_when_no_precision_met(self) -> None:
        from vaudeville.eval import CaseResult

        case_results = [
            CaseResult(text="fp", label="clean", predicted="violation", confidence=0.99)
            for _ in range(20)
        ]
        thresh, f1 = _find_best_threshold("test-rule", case_results)
        assert thresh == 0.0
        assert f1 == 0.0


class TestCalibrateRule:
    def test_refuses_under_minimum_cases(
        self,
        rules: dict[str, Rule],
        capsys: pytest.CaptureFixture[str],
        tmp_path: pathlib.Path,
    ) -> None:
        rule_file = tmp_path / "rule.yaml"
        rule_file.write_text(
            yaml.dump({"name": "violation-detector", "threshold": 0.5})
        )
        cases = [EvalCase(f"text {i}", "violation") for i in range(19)]
        target = CalibrateTarget("violation-detector", str(rule_file))
        result = calibrate_rule(target, cases, rules, MockBackend())
        assert result is None
        out = capsys.readouterr().out
        assert "minimum" in out
        assert str(MIN_CALIBRATION_CASES) in out

    def test_writes_threshold_to_yaml(
        self,
        rules: dict[str, Rule],
        capsys: pytest.CaptureFixture[str],
        tmp_path: pathlib.Path,
    ) -> None:
        from vaudeville.eval import CaseResult

        rule_yaml = {
            "name": "violation-detector",
            "event": "Stop",
            "prompt": "Classify:\n{text}\nVERDICT:",
            "action": "block",
            "threshold": 0.5,
            "message": "{reason}",
        }
        rule_file = tmp_path / "violation-detector.yaml"
        rule_file.write_text(yaml.dump(rule_yaml, sort_keys=False))

        cases = [EvalCase(f"v{i}", "violation") for i in range(15)] + [
            EvalCase(f"c{i}", "clean") for i in range(10)
        ]

        case_results = (
            [
                CaseResult(
                    text=f"v{i}",
                    label="violation",
                    predicted="violation",
                    confidence=0.9,
                )
                for i in range(15)
            ]
            + [
                CaseResult(
                    text=f"fp{i}", label="clean", predicted="violation", confidence=0.4
                )
                for i in range(3)
            ]
            + [
                CaseResult(
                    text=f"tn{i}", label="clean", predicted="clean", confidence=0.0
                )
                for i in range(7)
            ]
        )
        mock_results = EvalResults(rule="violation-detector", tp=15, fp=3, tn=7, fn=0)

        with patch(
            "vaudeville.eval.evaluate_rule",
            return_value=(mock_results, case_results),
        ):
            target = CalibrateTarget("violation-detector", str(rule_file))
            result = calibrate_rule(target, cases, rules, MockBackend())

        assert result == 0.45
        with open(rule_file) as f:
            updated = yaml.safe_load(f)
        assert updated["threshold"] == 0.45
        assert updated["name"] == "violation-detector"
        assert updated["event"] == "Stop"
        assert updated["action"] == "block"
        out = capsys.readouterr().out
        assert "Calibrated" in out

    def test_returns_none_when_no_threshold_meets_precision(
        self,
        rules: dict[str, Rule],
        capsys: pytest.CaptureFixture[str],
        tmp_path: pathlib.Path,
    ) -> None:
        from vaudeville.eval import CaseResult

        rule_file = tmp_path / "rule.yaml"
        rule_file.write_text(
            yaml.dump({"name": "violation-detector", "threshold": 0.5})
        )
        cases = [EvalCase(f"t{i}", "clean") for i in range(25)]
        case_results = [
            CaseResult(
                text=f"fp{i}", label="clean", predicted="violation", confidence=0.99
            )
            for i in range(25)
        ]
        mock_results = EvalResults(rule="violation-detector", fp=25)

        with patch(
            "vaudeville.eval.evaluate_rule",
            return_value=(mock_results, case_results),
        ):
            target = CalibrateTarget("violation-detector", str(rule_file))
            result = calibrate_rule(target, cases, rules, MockBackend())

        assert result is None
        out = capsys.readouterr().out
        assert "No threshold" in out
