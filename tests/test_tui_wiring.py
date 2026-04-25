"""Tests verifying TUI methods are called by orchestrate_tune and orchestrate_generate."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import FakeRalphRunner


class TestOrchestrateTuneTUIWiring:
    """TUI methods are invoked during orchestrate_tune execution."""

    def test_tui_update_phase_called_per_round(self, tmp_path: Path) -> None:
        """TUI.update_phase is called at start of each round and each sub-phase."""
        from vaudeville.orchestrator import Thresholds, orchestrate_tune

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        def mk_plan() -> None:
            state = tmp_path / "commands" / "tune" / "state"
            state.mkdir(parents=True, exist_ok=True)
            (state / "my-rule.plan.md").write_text("# Plan\n1. Fix\n")

        runner.add_response(
            mk_plan,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design", stderr=""
            ),
        )
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Tune", stderr=""
            ),
        )
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="JUDGE_DONE", stderr=""
            ),
        )

        tui = MagicMock()
        orchestrate_tune(
            "my-rule",
            Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85),
            rounds=3,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
            tui=tui,
        )

        # update_phase called for orchestrating, design, tune, judge
        assert tui.update_phase.call_count >= 4

    def test_tui_update_verdict_called_after_judge(self, tmp_path: Path) -> None:
        """TUI.update_verdict is called with the judge verdict string."""
        from vaudeville.orchestrator import Thresholds, orchestrate_tune

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        def mk_plan() -> None:
            state = tmp_path / "commands" / "tune" / "state"
            state.mkdir(parents=True, exist_ok=True)
            (state / "my-rule.plan.md").write_text("EMPTY_PLAN\n")

        runner.add_response(
            mk_plan,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design", stderr=""
            ),
        )
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="JUDGE_DONE", stderr=""
            ),
        )

        tui = MagicMock()
        orchestrate_tune(
            "my-rule",
            Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85),
            rounds=1,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
            tui=tui,
        )

        tui.update_verdict.assert_called_once_with("JUDGE_DONE")


class TestOrchestrateGenerateTUIWiring:
    def test_tui_update_phase_called_for_generate(self, tmp_path: Path) -> None:
        """TUI.update_phase('generate') is called before the generate phase."""
        from vaudeville.orchestrator import Thresholds, orchestrate_generate

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Generated", stderr=""
            ),
        )

        tui = MagicMock()
        with patch(
            "vaudeville.orchestrator._abandon._eval_rule",
            return_value=Thresholds(0.95, 0.80, 0.85),
        ):
            orchestrate_generate(
                instructions="test instructions",
                thresholds=Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85),
                rounds=1,
                tuner_iters=5,
                mode="shadow",
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
                rules_dir=str(rules_dir),
                tui=tui,
            )

        tui.update_phase.assert_called_with("generate")

    def test_instructions_none_calls_build_default(self, tmp_path: Path) -> None:
        """instructions=None → build_default_instructions is called."""
        from vaudeville.orchestrator import Thresholds, orchestrate_generate

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Generated", stderr=""
            ),
        )

        with (
            patch(
                "vaudeville.orchestrator._default_prompt.build_default_instructions",
                return_value="auto-generated instructions",
            ) as mock_build,
            patch(
                "vaudeville.orchestrator._abandon._eval_rule",
                return_value=Thresholds(0.95, 0.80, 0.85),
            ),
        ):
            rc = orchestrate_generate(
                instructions=None,
                thresholds=Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85),
                rounds=1,
                tuner_iters=5,
                mode="shadow",
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
                rules_dir=str(rules_dir),
            )

        assert rc == 0
        mock_build.assert_called_once_with(str(tmp_path))

        # Verify instructions were passed to ralph as --instructions arg
        gen_args = runner.calls[0][1]
        assert "--instructions" in gen_args
        idx = gen_args.index("--instructions")
        assert gen_args[idx + 1] == "auto-generated instructions"
