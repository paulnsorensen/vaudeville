"""Tests for vaudeville.orchestrator.orchestrate_generate."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import FakeRalphRunner


class TestOrchestrateGenerate:
    """Test generate orchestration: designer → eval rules → tune if needed."""

    def test_orchestrate_generate_all_rules_pass_immediately(self, tmp_path: Path) -> None:
        """All 3 rules pass eval immediately → no tune pipeline → exit 0."""
        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_generate,
        )

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"

        # Generate phase: writes 3 rule YAMLs
        def generate_side_effect() -> None:
            rules_dir.mkdir(parents=True, exist_ok=True)
            (rules_dir / "rule1.yaml").write_text("name: rule1\ntier: shadow\n")
            (rules_dir / "rule2.yaml").write_text("name: rule2\ntier: shadow\n")
            (rules_dir / "rule3.yaml").write_text("name: rule3\ntier: shadow\n")

        runner.add_response(
            generate_side_effect,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Generated 3 rules", stderr=""
            ),
        )

        thresholds = Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85)

        # Mock eval to always return thresholds met
        with patch(
            "vaudeville.orchestrator._abandon._eval_rule",
            return_value=thresholds,
        ):
            rc = orchestrate_generate(
                instructions="test instructions",
                thresholds=thresholds,
                rounds=1,
                tuner_iters=5,
                mode="shadow",
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
                rules_dir=str(rules_dir),
            )

        assert rc == 0

    def test_orchestrate_generate_one_rule_tunes(self, tmp_path: Path) -> None:
        """One rule fails eval → enters tune pipeline → completes."""
        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_generate,
        )

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"

        # Generate: writes 3 rules
        def generate_side_effect() -> None:
            rules_dir.mkdir(parents=True, exist_ok=True)
            (rules_dir / "good1.yaml").write_text("name: good1\ntier: shadow\n")
            (rules_dir / "good2.yaml").write_text("name: good2\ntier: shadow\n")
            (rules_dir / "bad.yaml").write_text("name: bad\ntier: shadow\n")

        runner.add_response(
            generate_side_effect,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Generated", stderr=""
            ),
        )

        thresholds = Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85)

        # good1, good2 pass; bad needs tuning
        eval_results = {
            "good1": thresholds,
            "good2": thresholds,
            "bad": None,  # None means didn't meet thresholds
        }

        def mock_eval(rule_name: str, project_root: str) -> Thresholds | None:
            return eval_results.get(rule_name)

        # Tune pipeline for "bad" rule (3 phases: design, tune, judge)
        def bad_design() -> None:
            state_dir = tmp_path / "commands" / "tune" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "bad.plan.md").write_text("# Plan\n")

        runner.add_response(
            bad_design,
            subprocess.CompletedProcess(args=["ralph"], returncode=0, stdout="Design", stderr=""),
        )

        runner.add_response(
            None,
            subprocess.CompletedProcess(args=["ralph"], returncode=0, stdout="Tune", stderr=""),
        )

        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Judge\nJUDGE_DONE",
                stderr="",
            ),
        )

        with patch("vaudeville.orchestrator._abandon._eval_rule", side_effect=mock_eval):
            rc = orchestrate_generate(
                instructions="test instructions",
                thresholds=thresholds,
                rounds=1,
                tuner_iters=5,
                mode="shadow",
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
                rules_dir=str(rules_dir),
            )

        assert rc == 0

    def test_eval_rule_parses_run_summary(self) -> None:
        """_eval_rule parses the rule's precision/recall/f1 from --json stdout."""
        from vaudeville.eval_report import RunSummary
        from vaudeville.orchestrator._abandon import _eval_rule

        summary = RunSummary.model_validate(
            {
                "passed": True,
                "rules": [
                    {
                        "rule": "rule",
                        "n": 10,
                        "tp": 8,
                        "fp": 0,
                        "tn": 2,
                        "fn": 0,
                        "precision": 0.92,
                        "recall": 0.81,
                        "f1": 0.86,
                        "passed": True,
                        "calibration": {"status": "n/a", "n": 0},
                    }
                ],
            }
        )
        fake = subprocess.CompletedProcess(
            args=["uv"], returncode=0, stdout=summary.to_json(), stderr=""
        )
        with patch("subprocess.run", return_value=fake):
            result = _eval_rule("rule", "/proj")
        assert result is not None
        assert result.p_min == 0.92
        assert result.r_min == 0.81
        assert result.f1_min == 0.86

    def test_eval_rule_invalid_json_returns_none(self) -> None:
        """Non-JSON stdout → None (no crash)."""
        from vaudeville.orchestrator._abandon import _eval_rule

        fake = subprocess.CompletedProcess(
            args=["uv"], returncode=1, stdout="not json at all", stderr="boom"
        )
        with patch("subprocess.run", return_value=fake):
            assert _eval_rule("rule", "/proj") is None

    def test_eval_rule_missing_rule_returns_none(self) -> None:
        """Valid RunSummary JSON without the requested rule → None."""
        from vaudeville.eval_report import RunSummary
        from vaudeville.orchestrator._abandon import _eval_rule

        summary = RunSummary.model_validate({"passed": True, "rules": []})
        fake = subprocess.CompletedProcess(
            args=["uv"], returncode=0, stdout=summary.to_json(), stderr=""
        )
        with patch("subprocess.run", return_value=fake):
            assert _eval_rule("rule", "/proj") is None

    def test_eval_rule_returns_none_when_uv_missing(self) -> None:
        """FileNotFoundError from subprocess.run → None (graceful degrade)."""
        from vaudeville.orchestrator._abandon import _eval_rule

        with patch("subprocess.run", side_effect=FileNotFoundError("uv not found")):
            assert _eval_rule("rule", "/proj") is None

    def test_eval_rule_invokes_json_flag(self) -> None:
        """_eval_rule always passes --json to the eval CLI argv."""
        from vaudeville.orchestrator._abandon import _eval_rule

        fake = subprocess.CompletedProcess(
            args=["uv"], returncode=0, stdout='{"rules": []}', stderr=""
        )
        with patch("subprocess.run", return_value=fake) as mock_run:
            _eval_rule("rule", "/proj")
        assert "--json" in mock_run.call_args.args[0]

    @pytest.mark.parametrize("returncode", [0, 1])
    def test_eval_rule_parses_regardless_of_exit_code(self, returncode: int) -> None:
        """A failing rule (exit 1) with valid JSON still parses."""
        from vaudeville.orchestrator._abandon import _eval_rule

        stdout = json.dumps(
            {"rules": [{"rule": "rule", "precision": 0.5, "recall": 0.6, "f1": 0.55}]}
        )
        fake = subprocess.CompletedProcess(
            args=["uv"], returncode=returncode, stdout=stdout, stderr=""
        )
        with patch("subprocess.run", return_value=fake):
            result = _eval_rule("rule", "/proj")
        assert result is not None
        assert result.p_min == 0.5

    @pytest.mark.parametrize(
        "stdout",
        [
            '{"foo": 1}',
            "[]",
            "null",
            "",
            'noise\n{"rules": [{"rule": "rule", "precision": 0.9, "recall": 0.9, "f1": 0.9}]}',
        ],
    )
    def test_eval_rule_returns_none_on_foreign_or_noisy_stdout(self, stdout: str) -> None:
        """Foreign JSON, empty stdout, and noise before JSON all return None."""
        from vaudeville.orchestrator._abandon import _eval_rule

        fake = subprocess.CompletedProcess(args=["uv"], returncode=0, stdout=stdout, stderr="")
        with patch("subprocess.run", return_value=fake):
            assert _eval_rule("rule", "/proj") is None

    def test_eval_rule_rejects_bool_metric_values(self) -> None:
        """A bool precision (int subtype in Python) is rejected, not coerced."""
        from vaudeville.orchestrator._abandon import _eval_rule

        stdout = json.dumps(
            {"rules": [{"rule": "rule", "precision": True, "recall": 0.6, "f1": 0.5}]}
        )
        fake = subprocess.CompletedProcess(args=["uv"], returncode=0, stdout=stdout, stderr="")
        with patch("subprocess.run", return_value=fake):
            assert _eval_rule("rule", "/proj") is None

    def test_eval_rule_partial_rule_shape_parses(self) -> None:
        """A rule entry with only rule/precision/recall/f1 still parses."""
        from vaudeville.orchestrator._abandon import _eval_rule

        stdout = json.dumps(
            {"rules": [{"rule": "rule", "precision": 0.9, "recall": 0.8, "f1": 0.85}]}
        )
        fake = subprocess.CompletedProcess(args=["uv"], returncode=0, stdout=stdout, stderr="")
        with patch("subprocess.run", return_value=fake):
            result = _eval_rule("rule", "/proj")
        assert result is not None
        assert result.f1_min == 0.85

    def test_extract_abandon_reason_strips_signal_line(self) -> None:
        """_extract_abandon_reason returns prose above JUDGE_* signal line."""
        from vaudeville.orchestrator._abandon import _extract_abandon_reason

        stdout = "analysis line one\nanalysis line two\nJUDGE_ABANDON"
        reason = _extract_abandon_reason(stdout)
        assert reason == "analysis line one\nanalysis line two"

    def test_extract_abandon_reason_empty_when_signal_first(self) -> None:
        """Judge output starting with signal → empty reason."""
        from vaudeville.orchestrator._abandon import _extract_abandon_reason

        assert _extract_abandon_reason("JUDGE_ABANDON") == ""

    def test_designer_ralph_failure_surfaces(self, tmp_path: Path) -> None:
        """Non-zero exit from the generate designer raises RalphError."""
        from vaudeville.orchestrator import (
            RalphError,
            Thresholds,
            orchestrate_generate,
        )

        runner = FakeRalphRunner()
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=3, stdout="", stderr="designer exploded"
            ),
        )

        with pytest.raises(RalphError, match="generate phase failed"):
            orchestrate_generate(
                instructions="anything",
                thresholds=Thresholds(0.95, 0.80, 0.85),
                rounds=1,
                tuner_iters=5,
                mode="shadow",
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
                rules_dir=str(tmp_path / ".vaudeville" / "rules"),
            )

    def test_orchestrate_generate_uses_rules_dir_for_snapshot(self, tmp_path: Path) -> None:
        """Snapshot uses rules_dir, not project_root/.vaudeville/rules."""
        from vaudeville.orchestrator import Thresholds, orchestrate_generate

        runner = FakeRalphRunner()
        custom_rules_dir = tmp_path / "custom" / "rules"

        def generate_side_effect() -> None:
            custom_rules_dir.mkdir(parents=True, exist_ok=True)
            (custom_rules_dir / "new-rule.yaml").write_text("name: new-rule\ntier: shadow\n")

        runner.add_response(
            generate_side_effect,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Generated", stderr=""
            ),
        )

        thresholds = Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85)

        with patch("vaudeville.orchestrator._abandon._eval_rule", return_value=thresholds):
            rc = orchestrate_generate(
                instructions="test",
                thresholds=thresholds,
                rounds=1,
                tuner_iters=5,
                mode="shadow",
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
                rules_dir=str(custom_rules_dir),
            )

        assert rc == 0
        # 1 call (generate) + eval returned passing → no tune
        assert len(runner.calls) == 1

    def test_orchestrate_generate_passes_rules_dir_to_designer(self, tmp_path: Path) -> None:
        """--rules_dir is included in the generate phase args."""
        from vaudeville.orchestrator import Thresholds, orchestrate_generate

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"

        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Generated", stderr=""
            ),
        )

        with patch(
            "vaudeville.orchestrator._abandon._eval_rule",
            return_value=Thresholds(0.95, 0.80, 0.85),
        ):
            orchestrate_generate(
                instructions="test",
                thresholds=Thresholds(0.95, 0.80, 0.85),
                rounds=1,
                tuner_iters=5,
                mode="shadow",
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
                rules_dir=str(rules_dir),
            )

        gen_args = runner.calls[0][1]
        assert "--rules_dir" in gen_args
        idx = gen_args.index("--rules_dir")
        assert gen_args[idx + 1] == str(rules_dir)
