"""Tests for vaudeville/eval.py — main() entrypoint."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from conftest import MockBackend
from vaudeville.core.rules import Rule
from vaudeville.eval import EvalCase

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
            patch("vaudeville.eval.load_rules_layered", return_value=_STUB_RULES),
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
            patch("vaudeville.eval.load_rules_layered", return_value=_STUB_RULES),
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
            patch("vaudeville.eval.load_rules_layered", return_value=_STUB_RULES),
            patch("vaudeville.eval.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval import main

                main()
        assert exc_info.value.code == 1

    def test_rules_dir_loads_from_specified_directory(self) -> None:
        """--rules-dir loads rules only from the given directory."""
        with tempfile.TemporaryDirectory() as rules_dir:
            rule_path = os.path.join(rules_dir, "test-rule.yaml")
            with open(rule_path, "w") as f:
                yaml.dump(
                    {
                        "name": "test-rule",
                        "event": "Stop",
                        "prompt": "Classify:\n{text}\nVERDICT:",
                        "action": "block",
                        "message": "{reason}",
                    },
                    f,
                )
            mock_backend = MockBackend(verdict="clean")
            mock_mlx_cls = MagicMock(return_value=mock_backend)
            with (
                patch(
                    "sys.argv",
                    ["eval", "--rules-dir", rules_dir, "--rule", "test-rule"],
                ),
                patch("vaudeville.server.MLXBackend", mock_mlx_cls),
                patch("vaudeville.eval.load_test_cases", return_value={}),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    from vaudeville.eval import main

                    main()
            # Exits 1 because no test suite, but load_rules_layered not called
            assert exc_info.value.code == 1

    def test_rules_dir_skips_layered_resolution(self) -> None:
        """--rules-dir bypasses load_rules_layered entirely."""
        with tempfile.TemporaryDirectory() as rules_dir:
            rule_path = os.path.join(rules_dir, "violation-detector.yaml")
            with open(rule_path, "w") as f:
                yaml.dump(
                    {
                        "name": "violation-detector",
                        "event": "Stop",
                        "prompt": "Classify:\n{text}\nVERDICT:",
                        "action": "block",
                        "message": "{reason}",
                    },
                    f,
                )
            mock_backend = MockBackend(verdict="clean")
            mock_mlx_cls = MagicMock(return_value=mock_backend)
            mock_layered = MagicMock(side_effect=AssertionError("should not be called"))
            with (
                patch("sys.argv", ["eval", "--rules-dir", rules_dir]),
                patch("vaudeville.server.MLXBackend", mock_mlx_cls),
                patch("vaudeville.eval.load_rules_layered", mock_layered),
                patch("vaudeville.eval.load_test_cases", return_value={}),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    from vaudeville.eval import main

                    main()
            assert exc_info.value.code == 0
            mock_layered.assert_not_called()

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
            patch("vaudeville.eval.load_rules_layered", return_value=_STUB_RULES),
            patch("vaudeville.eval.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval import main

                main()
        assert exc_info.value.code == 1
