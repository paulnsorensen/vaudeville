"""Tests for vaudeville/eval_cli.py — main() and helpers."""

from __future__ import annotations

import argparse
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from conftest import MockBackend
from vaudeville.core.rules import Rule
from vaudeville.eval import EvalCase
from vaudeville.eval_cli import _apply_extra_test_file

_STUB_RULE = Rule(
    name="violation-detector",
    event="Stop",
    prompt="Classify:\n{text}\nVERDICT:",
    context=[{"field": "last_assistant_message"}],
    action="block",
    message="{reason}",
)
_STUB_RULES = {"violation-detector": _STUB_RULE}


class TestMain:
    def test_exits_0_on_all_pass(self) -> None:
        mock_backend = MockBackend(verdict="clean")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        with (
            patch("sys.argv", ["eval"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value=_STUB_RULES),
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

                main()
        assert exc_info.value.code == 0

    def test_filters_to_single_rule_when_specified(self) -> None:
        mock_backend = MockBackend(verdict="clean")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        with (
            patch("sys.argv", ["eval", "--rule", "violation-detector"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value=_STUB_RULES),
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
            patch("sys.argv", ["eval", "--rule", "nonexistent-rule"]),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value=_STUB_RULES),
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
                ["eval", "--rule", "violation-detector", "--test-file", tf],
            ),
            patch("vaudeville.server.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value=_STUB_RULES),
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

                main()
        assert exc_info.value.code == 1


class TestApplyExtraTestFile:
    def _make_args(
        self, rule: str | None = None, test_file: str | None = None
    ) -> argparse.Namespace:
        return argparse.Namespace(rule=rule, test_file=test_file)

    def test_noop_when_no_test_file(self) -> None:
        suites: dict[str, list[EvalCase]] = {}
        _apply_extra_test_file(self._make_args(rule="r"), suites)
        assert suites == {}

    def test_noop_when_no_rule(self) -> None:
        suites: dict[str, list[EvalCase]] = {}
        _apply_extra_test_file(self._make_args(test_file="/tmp/f.yaml"), suites)
        assert suites == {}

    def test_merges_extra_cases(self) -> None:
        existing = EvalCase(text="old", label="clean")
        suites: dict[str, list[EvalCase]] = {"my-rule": [existing]}
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.dump(
                {"rule": "my-rule", "cases": [{"text": "new", "label": "violation"}]},
                f,
            )
        _apply_extra_test_file(
            self._make_args(rule="my-rule", test_file=f.name), suites
        )
        assert len(suites["my-rule"]) == 2
        assert suites["my-rule"][0].text == "old"
        assert suites["my-rule"][1].text == "new"

    def test_creates_suite_when_none_exists(self) -> None:
        suites: dict[str, list[EvalCase]] = {}
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.dump(
                {"rule": "new-rule", "cases": [{"text": "t", "label": "clean"}]},
                f,
            )
        _apply_extra_test_file(
            self._make_args(rule="new-rule", test_file=f.name), suites
        )
        assert len(suites["new-rule"]) == 1

    def test_exits_on_rule_mismatch(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.dump(
                {"rule": "other-rule", "cases": [{"text": "t", "label": "clean"}]},
                f,
            )
        with pytest.raises(SystemExit) as exc_info:
            _apply_extra_test_file(
                self._make_args(rule="my-rule", test_file=f.name), {}
            )
        assert exc_info.value.code == 1
