"""Tests for vaudeville/eval_cli.py — main() and helpers."""

from __future__ import annotations

import argparse
import os
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
        # threshold=0.0 so confidence=0 doesn't flip "violation" to "clean"
        stub_rule = Rule(
            name="violation-detector",
            event="Stop",
            prompt="Classify:\n{text}\nVERDICT:",
            context=[{"field": "last_assistant_message"}],
            action="block",
            message="{reason}",
            threshold=0.0,
        )
        mock_backend = MockBackend(verdict="violation")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        stub_suites = {"violation-detector": [EvalCase(text="text", label="violation")]}
        with (
            patch("sys.argv", ["eval", "--no-daemon"]),
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval_cli.load_rules_layered",
                return_value={"violation-detector": stub_rule},
            ),
            patch("vaudeville.eval_cli.load_test_cases", return_value=stub_suites),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

                main()
        assert exc_info.value.code == 0

    def test_filters_to_single_rule_when_specified(self) -> None:
        mock_backend = MockBackend(verdict="clean")
        mock_mlx_cls = MagicMock(return_value=mock_backend)
        with (
            patch("sys.argv", ["eval", "--no-daemon", "--rule", "violation-detector"]),
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
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
            patch("sys.argv", ["eval", "--no-daemon", "--rule", "nonexistent-rule"]),
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value=_STUB_RULES),
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

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
                    [
                        "eval",
                        "--no-daemon",
                        "--rules-dir",
                        rules_dir,
                        "--rule",
                        "test-rule",
                    ],
                ),
                patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
                patch("vaudeville.eval_cli.load_test_cases", return_value={}),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    from vaudeville.eval_cli import main

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
            stub_rule = Rule(
                name="violation-detector",
                event="Stop",
                prompt="Classify:\n{text}\nVERDICT:",
                context=[{"field": "last_assistant_message"}],
                action="block",
                message="{reason}",
                threshold=0.0,
            )
            mock_backend = MockBackend(verdict="violation")
            mock_mlx_cls = MagicMock(return_value=mock_backend)
            mock_layered = MagicMock(side_effect=AssertionError("should not be called"))
            stub_suites = {
                "violation-detector": [EvalCase(text="text", label="violation")]
            }
            with (
                patch("sys.argv", ["eval", "--no-daemon", "--rules-dir", rules_dir]),
                patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
                patch("vaudeville.eval_cli.load_rules_layered", mock_layered),
                patch(
                    "vaudeville.eval_cli.load_rules",
                    return_value={"violation-detector": stub_rule},
                ),
                patch("vaudeville.eval_cli.load_test_cases", return_value=stub_suites),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    from vaudeville.eval_cli import main

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
                [
                    "eval",
                    "--no-daemon",
                    "--rule",
                    "violation-detector",
                    "--test-file",
                    tf,
                ],
            ),
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
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

    def test_test_file_matching_rule_merges_cases(self) -> None:
        """--test-file with matching --rule merges extra cases into the suite."""
        from vaudeville.eval import EvalCase
        from vaudeville.core.rules import Rule

        zero_threshold_rule = Rule(
            name="violation-detector",
            event="Stop",
            prompt="Classify:\n{text}\nVERDICT:",
            context=[{"field": "last_assistant_message"}],
            action="block",
            message="{reason}",
            threshold=0.0,
        )
        mock_mlx_cls = MagicMock(return_value=MockBackend(verdict="violation"))
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.dump(
                {
                    "rule": "violation-detector",
                    "cases": [{"text": "extra text", "label": "violation"}],
                },
                f,
            )
            tf = f.name
        merged_suites: dict[str, list[EvalCase]] = {}

        def capture_suites(args, rules, suites, backend):  # type: ignore[no-untyped-def]
            merged_suites.update(suites)
            from vaudeville.eval import EvalResults

            return (
                True,
                {"violation-detector": EvalResults(rule="violation-detector", tp=2)},
                [],
            )

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
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
            patch(
                "vaudeville.eval_cli.load_rules_layered",
                return_value={"violation-detector": zero_threshold_rule},
            ),
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={
                    "violation-detector": [EvalCase("existing text", "violation")]
                },
            ),
            patch("vaudeville.eval_report.run_evaluations", side_effect=capture_suites),
        ):
            with pytest.raises(SystemExit):
                from vaudeville.eval_cli import main

                main()
        # Both the existing case and the extra case should be in the merged suite
        merged = merged_suites.get("violation-detector", [])
        assert len(merged) == 2
        texts = [c.text for c in merged]
        assert "existing text" in texts
        assert "extra text" in texts

    def test_eval_log_writes_log_when_results_exist(self) -> None:
        """--eval-log causes write_eval_log to be called when results exist."""
        from vaudeville.eval import EvalCase

        mock_mlx_cls = MagicMock(return_value=MockBackend(verdict="violation"))
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name
        with (
            patch(
                "sys.argv",
                [
                    "eval",
                    "--no-daemon",
                    "--rule",
                    "violation-detector",
                    "--eval-log",
                    log_path,
                ],
            ),
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value=_STUB_RULES),
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={"violation-detector": [EvalCase("text", "violation")]},
            ),
        ):
            with pytest.raises(SystemExit):
                from vaudeville.eval_cli import main

                main()
        import json
        import os

        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert entry["model"] == "mlx-community/Phi-4-mini-instruct-4bit"
        assert "violation-detector" in entry["rules"]
        os.unlink(log_path)

    def test_threshold_sweep_flag_calls_sweep(self) -> None:
        """--threshold-sweep invokes threshold_sweep."""
        from vaudeville.eval import EvalCase

        mock_mlx_cls = MagicMock(return_value=MockBackend(verdict="violation"))
        mock_sweep = MagicMock()
        with (
            patch(
                "sys.argv",
                [
                    "eval",
                    "--no-daemon",
                    "--rule",
                    "violation-detector",
                    "--threshold-sweep",
                ],
            ),
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value=_STUB_RULES),
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={"violation-detector": [EvalCase("text", "violation")]},
            ),
            patch("vaudeville.eval_report.threshold_sweep", mock_sweep),
        ):
            with pytest.raises(SystemExit):
                from vaudeville.eval_cli import main

                main()
        mock_sweep.assert_called_once()

    def test_calibrate_no_test_suite_exits_1(self) -> None:
        """--calibrate exits 1 when no test suite exists for the rule."""
        mock_mlx_cls = MagicMock(return_value=MockBackend())
        with (
            patch(
                "sys.argv", ["eval", "--no-daemon", "--calibrate", "violation-detector"]
            ),
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value=_STUB_RULES),
            patch("vaudeville.eval_cli.load_test_cases", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

                main()
        assert exc_info.value.code == 1

    def test_calibrate_no_rule_definition_exits_1(self) -> None:
        """--calibrate exits 1 when rule is in test_suites but not in rules dict."""
        from vaudeville.eval import EvalCase

        mock_mlx_cls = MagicMock(return_value=MockBackend())
        with (
            patch(
                "sys.argv", ["eval", "--no-daemon", "--calibrate", "violation-detector"]
            ),
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value={}),
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={"violation-detector": [EvalCase("t", "clean")]},
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

                main()
        assert exc_info.value.code == 1

    def test_calibrate_rule_file_not_found_exits_1(self) -> None:
        """--calibrate exits 1 when the rule YAML file cannot be located."""
        from vaudeville.eval import EvalCase

        mock_mlx_cls = MagicMock(return_value=MockBackend())
        with (
            patch(
                "sys.argv", ["eval", "--no-daemon", "--calibrate", "violation-detector"]
            ),
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value=_STUB_RULES),
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={"violation-detector": [EvalCase("t", "clean")]},
            ),
            patch("vaudeville.eval_calibrate.find_rule_file", return_value=None),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

                main()
        assert exc_info.value.code == 1

    def test_calibrate_success_exits_0(self) -> None:
        """--calibrate exits 0 when calibrate_rule returns a threshold."""
        import os
        from vaudeville.eval import EvalCase

        mock_mlx_cls = MagicMock(return_value=MockBackend())
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.dump(
                {
                    "name": "violation-detector",
                    "event": "Stop",
                    "prompt": "Classify:\n{text}\nVERDICT:",
                    "action": "block",
                    "message": "{reason}",
                    "threshold": 0.5,
                },
                f,
            )
            rule_file = f.name

        with (
            patch(
                "sys.argv", ["eval", "--no-daemon", "--calibrate", "violation-detector"]
            ),
            patch("vaudeville.server.mlx_backend.MLXBackend", mock_mlx_cls),
            patch("vaudeville.eval_cli.load_rules_layered", return_value=_STUB_RULES),
            patch(
                "vaudeville.eval_cli.load_test_cases",
                return_value={"violation-detector": [EvalCase("t", "clean")]},
            ),
            patch(
                "vaudeville.eval_calibrate.find_rule_file",
                return_value=rule_file,
            ),
            patch(
                "vaudeville.eval_calibrate.calibrate_rule",
                return_value=0.5,
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from vaudeville.eval_cli import main

                main()
        os.unlink(rule_file)
        assert exc_info.value.code == 0
