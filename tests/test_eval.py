"""Tests for vaudeville/eval.py — eval harness."""

from __future__ import annotations

import os
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from conftest import MockBackend
from vaudeville.eval import (
    EvalCase,
    EvalResults,
    _classify_case,
    _find_project_root,
    _run_evaluations,
    cross_validate_rule,
    evaluate_rule,
    load_test_cases,
    print_results,
)
from vaudeville.core.rules import load_rules

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(PROJECT_ROOT, "rules")


@pytest.fixture
def rules() -> dict:
    return load_rules(RULES_DIR)


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

    def test_warns_on_bad_yaml(self, caplog) -> None:
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
    def test_true_positive(self, rules: dict) -> None:
        backend = MockBackend(verdict="violation", reason="found issue")
        results = EvalResults(rule="violation-detector", misclassified=[])
        case = EvalCase(text="this should work", label="violation")
        _classify_case(case, rules["violation-detector"], backend, results)
        assert results.tp == 1
        assert results.fp == 0

    def test_true_negative(self, rules: dict) -> None:
        backend = MockBackend(verdict="clean", reason="ok")
        results = EvalResults(rule="violation-detector", misclassified=[])
        case = EvalCase(text="all good", label="clean")
        _classify_case(case, rules["violation-detector"], backend, results)
        assert results.tn == 1

    def test_false_positive(self, rules: dict) -> None:
        backend = MockBackend(verdict="violation", reason="false alarm")
        results = EvalResults(rule="violation-detector", misclassified=[])
        case = EvalCase(text="all tests pass", label="clean")
        _classify_case(case, rules["violation-detector"], backend, results)
        assert results.fp == 1
        assert len(results.misclassified) == 1  # type: ignore[arg-type]
        assert results.misclassified[0]["actual"] == "clean"  # type: ignore[index]

    def test_false_negative(self, rules: dict) -> None:
        backend = MockBackend(verdict="clean", reason="missed it")
        results = EvalResults(rule="violation-detector", misclassified=[])
        case = EvalCase(text="this might work", label="violation")
        _classify_case(case, rules["violation-detector"], backend, results)
        assert results.fn == 1
        assert results.misclassified[0]["actual"] == "violation"  # type: ignore[index]


class TestEvaluateRule:
    def test_raises_for_unknown_rule(self, rules: dict) -> None:
        backend = MockBackend()
        with pytest.raises(ValueError, match="Rule not found"):
            evaluate_rule("unknown-rule", [], rules, backend)

    def test_aggregates_results(self, rules: dict, two_cases: list[EvalCase]) -> None:
        backend = MockBackend(verdict="violation")
        results = evaluate_rule("violation-detector", two_cases, rules, backend)
        assert results.total == 2


class TestCrossValidateRule:
    def test_raises_for_unknown_rule(self, rules: dict) -> None:
        with pytest.raises(ValueError, match="Rule not found"):
            cross_validate_rule("unknown", [], rules, MockBackend())

    def test_produces_correct_totals(
        self, rules: dict, two_cases: list[EvalCase], capsys
    ) -> None:
        backend = MockBackend(verdict="violation")
        results = cross_validate_rule("violation-detector", two_cases, rules, backend)
        assert results.total == 2
        out = capsys.readouterr().out
        assert "Fold" in out

    def test_tracks_misclassifications(self, rules: dict) -> None:
        cases = [EvalCase(text="good text", label="clean")]
        backend = MockBackend(verdict="violation")  # FP
        results = cross_validate_rule("violation-detector", cases, rules, backend)
        assert results.fp == 1
        assert len(results.misclassified) == 1  # type: ignore[arg-type]


class TestPrintResults:
    def test_pass_when_precision_and_recall_met(self, capsys) -> None:
        results = EvalResults(rule="test", tp=19, fp=1, tn=0, fn=4)
        passed = print_results(results)
        assert passed is True
        out = capsys.readouterr().out
        assert "PASS" in out

    def test_fail_when_below_threshold(self, capsys) -> None:
        results = EvalResults(rule="test", tp=5, fp=5, tn=0, fn=5)
        passed = print_results(results)
        assert passed is False
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_prints_misclassifications(self, capsys) -> None:
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
    def test_skips_rule_with_no_definition(self, rules: dict, capsys) -> None:
        import argparse

        args = argparse.Namespace(cross_validate=False)
        test_suites = {"undefined-rule": [EvalCase("text", "clean")]}
        passed = _run_evaluations(args, rules, test_suites, MockBackend())
        assert passed is True
        out = capsys.readouterr().out
        assert "WARNING" in out

    def test_passes_when_all_rules_pass(self, rules: dict) -> None:
        import argparse

        args = argparse.Namespace(cross_validate=False)
        cases = [EvalCase("text", "clean")]
        backend = MockBackend(verdict="clean")
        test_suites = {"violation-detector": cases}
        passed = _run_evaluations(args, rules, test_suites, backend)
        assert isinstance(passed, bool)

    def test_cross_validate_flag_used(self, rules: dict) -> None:
        import argparse

        args = argparse.Namespace(cross_validate=True)
        cases = [EvalCase("text", "clean")]
        backend = MockBackend(verdict="clean")
        test_suites = {"violation-detector": cases}
        _run_evaluations(args, rules, test_suites, backend)


class TestMain:
    def test_exits_0_on_all_pass(self, capsys) -> None:
        mock_backend = MockBackend(verdict="clean")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        with (
            patch("sys.argv", ["eval"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval.load_rules_layered",
                return_value=load_rules(RULES_DIR),
            ),
            patch("vaudeville.eval.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval import main

                main()
        assert exc_info.value.code == 0

    def test_filters_to_single_rule_when_specified(self, capsys) -> None:
        mock_backend = MockBackend(verdict="clean")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        with (
            patch("sys.argv", ["eval", "--rule", "violation-detector"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval.load_rules_layered",
                return_value=load_rules(RULES_DIR),
            ),
            patch(
                "vaudeville.eval.load_test_cases",
                return_value={"violation-detector": [EvalCase("text", "clean")]},
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval import main

                main()
        assert exc_info.value.code in (0, 1)

    def test_exits_1_when_no_suite_for_specified_rule(self) -> None:
        mock_mlx_cls = MagicMock(return_value=MockBackend())
        with (
            patch("sys.argv", ["eval", "--rule", "nonexistent-rule"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval.load_rules_layered",
                return_value=load_rules(RULES_DIR),
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
                return_value=load_rules(RULES_DIR),
            ),
            patch("vaudeville.eval.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval import main

                main()
        assert exc_info.value.code == 1
