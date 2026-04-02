"""Tests for vaudeville/eval.py — main() entrypoint."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from conftest import MockBackend
from vaudeville.eval import EvalCase
from vaudeville.core.rules import load_rules

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(PROJECT_ROOT, "rules")


class TestMain:
    def test_exits_0_on_all_pass(self) -> None:
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

    def test_filters_to_single_rule_when_specified(self) -> None:
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
