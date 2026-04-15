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
    CaseResult,
    EvalCase,
    EvalResults,
    classify_case,
    evaluate_rule,
    load_test_cases,
)
from vaudeville.eval_cli import (
    _emit_jsonl,
    _find_project_root,
)
from vaudeville.eval_report import (
    MIN_CALIBRATION_CASES,
    _find_best_threshold,
    _git_head,
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
    def test_oserror_on_listdir_returns_empty(self) -> None:
        with patch("vaudeville.eval.os.listdir", side_effect=OSError):
            assert load_test_cases("/nonexistent/dir") == {}

    def test_skips_non_yaml_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "readme.txt"), "w").close()
            assert load_test_cases(tmp) == {}

    def test_warns_on_bad_yaml(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        with tempfile.TemporaryDirectory() as tmp:
            bad_file = os.path.join(tmp, "bad.yaml")
            with open(bad_file, "w") as f:
                f.write(": invalid: yaml: {{{")
            with caplog.at_level(logging.WARNING):
                result = load_test_cases(tmp)
        assert result == {}

    def test_loads_valid_test_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.yaml")
            with open(path, "w") as f:
                yaml.dump(
                    {
                        "rule": "test-rule",
                        "cases": [
                            {"text": "example text", "label": "clean"},
                        ],
                    },
                    f,
                )
            result = load_test_cases(tmp)
        assert "test-rule" in result
        assert len(result["test-rule"]) == 1


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


class TestBuildBackend:
    def test_returns_mlx_backend_with_no_daemon(self) -> None:
        import argparse

        from vaudeville.eval_cli import _build_backend

        args = argparse.Namespace(model="test-model", no_daemon=True)
        mock_instance = MagicMock()
        mock_mlx = MagicMock(return_value=mock_instance)
        with patch("vaudeville.server.MLXBackend", mock_mlx):
            backend = _build_backend(args)
        mock_mlx.assert_called_once_with("test-model")
        assert backend is mock_instance

    def test_falls_back_to_mlx_when_daemon_unavailable(self) -> None:
        import argparse

        from vaudeville.eval_cli import _build_backend

        args = argparse.Namespace(model="test-model", no_daemon=False)
        mock_instance = MagicMock()
        mock_mlx = MagicMock(return_value=mock_instance)
        with (
            patch(
                "vaudeville.server.daemon_backend.daemon_is_alive",
                return_value=False,
            ),
            patch("vaudeville.server.MLXBackend", mock_mlx),
        ):
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
        passed, all_results, _ = run_evaluations(
            args, rules, test_suites, MockBackend()
        )
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
        passed, all_results, _ = run_evaluations(args, rules, test_suites, backend)
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
            patch("sys.argv", ["eval", "--no-daemon"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval_cli.load_rules_layered",
                return_value={
                    "violation-detector": Rule(
                        name="violation-detector",
                        event="Stop",
                        prompt="Classify:\n{text}\nVERDICT:",
                        context=[{"field": "last_assistant_message"}],
                        action="block",
                        message="{reason}",
                    ),
                },
            ),
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

                main()
        assert exc_info.value.code == 0

    def test_filters_to_single_rule_when_specified(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_backend = MockBackend(verdict="clean")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        with (
            patch("sys.argv", ["eval", "--no-daemon", "--rule", "violation-detector"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval_cli.load_rules_layered",
                return_value={
                    "violation-detector": Rule(
                        name="violation-detector",
                        event="Stop",
                        prompt="Classify:\n{text}\nVERDICT:",
                        context=[{"field": "last_assistant_message"}],
                        action="block",
                        message="{reason}",
                    ),
                },
            ),
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={"violation-detector": [EvalCase("text", "clean")]},
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

                main()
        assert (
            exc_info.value.code == 1
        )  # clean backend + clean case → 0 TP → fails threshold

    def test_exits_1_when_no_suite_for_specified_rule(self) -> None:
        mock_mlx_cls = MagicMock(return_value=MockBackend())
        with (
            patch("sys.argv", ["eval", "--no-daemon", "--rule", "nonexistent-rule"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval_cli.load_rules_layered",
                return_value={
                    "violation-detector": Rule(
                        name="violation-detector",
                        event="Stop",
                        prompt="Classify:\n{text}\nVERDICT:",
                        context=[{"field": "last_assistant_message"}],
                        action="block",
                        message="{reason}",
                    ),
                },
            ),
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

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
                [
                    "eval",
                    "--no-daemon",
                    "--rule",
                    "violation-detector",
                    "--test-file",
                    tf,
                ],
            ),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval_cli.load_rules_layered",
                return_value={
                    "violation-detector": Rule(
                        name="violation-detector",
                        event="Stop",
                        prompt="Classify:\n{text}\nVERDICT:",
                        context=[{"field": "last_assistant_message"}],
                        action="block",
                        message="{reason}",
                    ),
                },
            ),
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

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

        case_results = (
            [
                CaseResult(
                    rule="test-rule",
                    case_id=i,
                    text="v",
                    label="violation",
                    predicted="violation",
                    confidence=0.9,
                )
                for i in range(19)
            ]
            + [
                CaseResult(
                    rule="test-rule",
                    case_id=19 + i,
                    text="fp",
                    label="clean",
                    predicted="violation",
                    confidence=0.4,
                )
                for i in range(2)
            ]
            + [
                CaseResult(
                    rule="test-rule",
                    case_id=21 + i,
                    text="c",
                    label="clean",
                    predicted="clean",
                    confidence=0.0,
                )
                for i in range(5)
            ]
        )
        thresh, f1 = _find_best_threshold("test-rule", case_results)
        assert thresh == 0.45
        assert f1 > 0.0

    def test_returns_zero_when_no_precision_met(self) -> None:

        case_results = [
            CaseResult(
                rule="test-rule",
                case_id=i,
                text="fp",
                label="clean",
                predicted="violation",
                confidence=0.99,
            )
            for i in range(20)
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
        result = calibrate_rule(
            "violation-detector", cases, rules, MockBackend(), str(rule_file)
        )
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
                    rule="violation-detector",
                    case_id=i,
                    text=f"v{i}",
                    label="violation",
                    predicted="violation",
                    confidence=0.9,
                )
                for i in range(15)
            ]
            + [
                CaseResult(
                    rule="violation-detector",
                    case_id=15 + i,
                    text=f"fp{i}",
                    label="clean",
                    predicted="violation",
                    confidence=0.4,
                )
                for i in range(3)
            ]
            + [
                CaseResult(
                    rule="violation-detector",
                    case_id=18 + i,
                    text=f"tn{i}",
                    label="clean",
                    predicted="clean",
                    confidence=0.0,
                )
                for i in range(7)
            ]
        )
        mock_results = EvalResults(rule="violation-detector", tp=15, fp=3, tn=7, fn=0)

        with patch(
            "vaudeville.eval.evaluate_rule",
            return_value=(mock_results, case_results),
        ):
            result = calibrate_rule(
                "violation-detector", cases, rules, MockBackend(), str(rule_file)
            )

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

        rule_file = tmp_path / "rule.yaml"
        rule_file.write_text(
            yaml.dump({"name": "violation-detector", "threshold": 0.5})
        )
        cases = [EvalCase(f"t{i}", "clean") for i in range(25)]
        case_results = [
            CaseResult(
                rule="violation-detector",
                case_id=i,
                text=f"fp{i}",
                label="clean",
                predicted="violation",
                confidence=0.99,
            )
            for i in range(25)
        ]
        mock_results = EvalResults(rule="violation-detector", fp=25)

        with patch(
            "vaudeville.eval.evaluate_rule",
            return_value=(mock_results, case_results),
        ):
            result = calibrate_rule(
                "violation-detector", cases, rules, MockBackend(), str(rule_file)
            )

        assert result is None
        out = capsys.readouterr().out
        assert "No threshold" in out


class TestCaseResultToJsonl:
    def test_to_jsonl_dict_fields(self) -> None:
        cr = CaseResult(
            rule="test-rule",
            case_id=3,
            text="sample text",
            label="violation",
            predicted="clean",
            confidence=0.8765,
        )
        d = cr.to_jsonl_dict()
        assert d["rule"] == "test-rule"
        assert d["case_id"] == 3
        assert d["expected"] == "violation"
        assert d["predicted"] == "clean"
        assert d["confidence"] == 0.8765
        assert d["text"] == "sample text"

    def test_confidence_rounds_to_4_decimals(self) -> None:
        cr = CaseResult(
            rule="r",
            case_id=0,
            text="t",
            label="clean",
            predicted="clean",
            confidence=0.123456789,
        )
        assert cr.to_jsonl_dict()["confidence"] == 0.1235


class TestEmitJsonl:
    def test_emits_one_line_per_case(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        cases = [
            CaseResult(
                rule="r1",
                case_id=0,
                text="a",
                label="violation",
                predicted="violation",
                confidence=0.9,
            ),
            CaseResult(
                rule="r1",
                case_id=1,
                text="b",
                label="clean",
                predicted="clean",
                confidence=0.1,
            ),
        ]
        _emit_jsonl(cases)
        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["rule"] == "r1"
        assert first["case_id"] == 0
        assert first["expected"] == "violation"
        second = json.loads(lines[1])
        assert second["case_id"] == 1
        assert second["expected"] == "clean"

    def test_emits_nothing_for_empty_list(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _emit_jsonl([])
        out = capsys.readouterr().out
        assert out == ""


class TestEvaluateRuleSetsRuleAndCaseId:
    def test_case_results_have_rule_and_case_id(self, rules: dict[str, Rule]) -> None:
        backend = MockBackend(verdict="violation")
        cases = [
            EvalCase(text="text1", label="violation"),
            EvalCase(text="text2", label="clean"),
        ]
        _, case_results = evaluate_rule("violation-detector", cases, rules, backend)
        assert len(case_results) == 2
        assert case_results[0].rule == "violation-detector"
        assert case_results[0].case_id == 0
        assert case_results[1].rule == "violation-detector"
        assert case_results[1].case_id == 1


class TestRunEvaluationsReturnsCaseResults:
    def test_returns_case_results(self, rules: dict[str, Rule]) -> None:
        import argparse

        args = argparse.Namespace(cross_validate=False)
        cases = [
            EvalCase("v", "violation"),
            EvalCase("c", "clean"),
        ]
        backend = MockBackend(verdict="violation")
        test_suites = {"violation-detector": cases}
        _, _, case_results = run_evaluations(args, rules, test_suites, backend)
        assert len(case_results) == 2
        assert all(cr.rule == "violation-detector" for cr in case_results)

    def test_cross_validate_returns_empty_case_results(
        self, rules: dict[str, Rule]
    ) -> None:
        import argparse

        args = argparse.Namespace(cross_validate=True)
        cases = [EvalCase("text", "clean")]
        backend = MockBackend(verdict="clean")
        test_suites = {"violation-detector": cases}
        with patch("vaudeville.eval_report.cross_validate_rule") as mock_cv:
            mock_cv.return_value = EvalResults(rule="violation-detector", tp=1)
            _, _, case_results = run_evaluations(args, rules, test_suites, backend)
        assert case_results == []


class TestJsonFlag:
    def test_json_flag_emits_jsonl(self) -> None:
        mock_backend = MockBackend(verdict="violation")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        with (
            patch(
                "sys.argv",
                ["eval", "--no-daemon", "--rule", "violation-detector", "--json"],
            ),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval_cli.load_rules_layered",
                return_value={
                    "violation-detector": Rule(
                        name="violation-detector",
                        event="Stop",
                        prompt="Classify:\n{text}\nVERDICT:",
                        context=[{"field": "last_assistant_message"}],
                        action="block",
                        message="{reason}",
                    ),
                },
            ),
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={
                    "violation-detector": [
                        EvalCase("hedging text", "violation"),
                        EvalCase("clean text", "clean"),
                    ],
                },
            ),
            pytest.raises(SystemExit),
        ):
            import json
            import io
            import sys

            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                from vaudeville.eval_cli import main

                main()
            finally:
                sys.stdout = old_stdout

            output = captured.getvalue()
            json_lines = [
                line for line in output.strip().split("\n") if line.startswith("{")
            ]
            assert len(json_lines) == 2
            first = json.loads(json_lines[0])
            assert "rule" in first
            assert "expected" in first
            assert "predicted" in first
            assert "confidence" in first

    def test_no_json_flag_shows_summary(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_backend = MockBackend(verdict="clean")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        with (
            patch("sys.argv", ["eval", "--no-daemon"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval_cli.load_rules_layered",
                return_value={
                    "violation-detector": Rule(
                        name="violation-detector",
                        event="Stop",
                        prompt="Classify:\n{text}\nVERDICT:",
                        context=[{"field": "last_assistant_message"}],
                        action="block",
                        message="{reason}",
                    ),
                },
            ),
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
            pytest.raises(SystemExit),
        ):
            from vaudeville.eval_cli import main

            main()
        out = capsys.readouterr().out
        assert "ALL RULES PASS" in out
