"""Tests for vaudeville.orchestrator.orchestrate_tune."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import FakeRalphRunner


class TestOrchestrateTune:
    """Test the tune orchestration loop: round1 DONE, multi-round CONTINUE, RAISE, ABANDON."""

    def test_capture_eval_log_runs_before_design_phase(self, tmp_path: Path) -> None:
        """Each design round prefills .vaudeville/logs/eval-{rule}.log so the
        Designer never falls back to writing 'Run eval first' meta-items that
        the mechanical Tuner cannot execute."""
        from unittest.mock import patch

        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_tune,
        )

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        captured_calls: list[tuple[str, str]] = []

        def fake_capture(rule_name: str, project_root: str) -> str:
            captured_calls.append((rule_name, project_root))
            return "fake eval stdout"

        def design_side_effect() -> None:
            # Design must observe the prefilled log on disk, so it should
            # have been written before this side-effect runs.
            log_path = tmp_path / ".vaudeville" / "logs" / "eval-test-rule.log"
            assert log_path.parent.exists() or log_path.exists() or captured_calls, (
                "capture_eval_log must be invoked before the design ralph call"
            )
            state_dir = tmp_path / "commands" / "tune" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "test-rule.plan.md").write_text("EMPTY_PLAN\n")

        runner.add_response(
            design_side_effect,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="", stderr=""
            ),
        )
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="JUDGE_DONE", stderr=""
            ),
        )

        with patch(
            "vaudeville.orchestrator._abandon.capture_eval_log",
            side_effect=fake_capture,
        ):
            rc = orchestrate_tune(
                "test-rule",
                Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85),
                rounds=1,
                tuner_iters=5,
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
                rules_dir=str(rules_dir),
            )

        assert rc == 0
        assert captured_calls == [("test-rule", str(tmp_path))]

    def test_capture_eval_log_skipped_when_design_skipped(self, tmp_path: Path) -> None:
        """JUDGE_CONTINUE_TUNE_MORE skips design AND skips eval-log prefill —
        we don't want to re-run eval if the Designer isn't going to read it."""
        from unittest.mock import patch

        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_tune,
        )

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        captured_calls: list[tuple[str, str]] = []

        def fake_capture(rule_name: str, project_root: str) -> str:
            captured_calls.append((rule_name, project_root))
            return "ok"

        def r1_design() -> None:
            state_dir = tmp_path / "commands" / "tune" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "r.plan.md").write_text("- [ ] do thing\n")

        runner.add_response(
            r1_design,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="", stderr=""
            ),
        )
        # tune
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="", stderr=""
            ),
        )
        # judge → CONTINUE_TUNE_MORE (so round 2 skips design)
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="JUDGE_CONTINUE_TUNE_MORE",
                stderr="",
            ),
        )
        # round 2 tune
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="", stderr=""
            ),
        )
        # round 2 judge → DONE
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="JUDGE_DONE", stderr=""
            ),
        )

        with patch(
            "vaudeville.orchestrator._abandon.capture_eval_log",
            side_effect=fake_capture,
        ):
            orchestrate_tune(
                "r",
                Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85),
                rounds=2,
                tuner_iters=5,
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
                rules_dir=str(rules_dir),
            )

        # Only round 1 design ran → only one capture
        assert len(captured_calls) == 1

    def test_orchestrate_tune_round1_done(self, tmp_path: Path) -> None:
        """Round 1: design → tune → judge returns JUDGE_DONE → exit cleanly."""
        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_tune,
        )

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        # Design response (writes empty plan signal)
        def design_side_effect() -> None:
            state_dir = tmp_path / "commands" / "tune" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "test-rule.plan.md").write_text("# Design Plan\nEMPTY_PLAN\n")

        runner.add_response(
            design_side_effect,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design output", stderr=""
            ),
        )

        # Tune would run but plan is empty, so skipped

        # Judge response
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Judge analysis\nJUDGE_DONE",
                stderr="",
            ),
        )

        thresholds = Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85)
        rc = orchestrate_tune(
            "test-rule",
            thresholds,
            rounds=1,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
        )

        assert rc == 0

    def test_orchestrate_tune_multi_round_continue_to_done(
        self, tmp_path: Path
    ) -> None:
        """Multi-round: CONTINUE_TUNE_MORE skips design in round 2 → tune+judge → DONE."""
        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_tune,
        )

        runner = FakeRalphRunner()
        commands_dir = str(tmp_path / "commands")
        design_dir = str(tmp_path / "commands" / "design")
        tune_dir = str(tmp_path / "commands" / "tune")
        judge_dir = str(tmp_path / "commands" / "judge")
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        # Round 1: design
        def r1_design() -> None:
            state_dir = tmp_path / "commands" / "tune" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "test-rule.plan.md").write_text("# Design\n1. Fix prompt\n")

        runner.add_response(
            r1_design,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design", stderr=""
            ),
        )

        # Round 1: tune
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Tuner output",
                stderr="",
            ),
        )

        # Round 1: judge → CONTINUE_TUNE_MORE (design skipped next round)
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Judge analysis\nJUDGE_CONTINUE_TUNE_MORE",
                stderr="",
            ),
        )

        # Round 2: NO design (TUNE_MORE reuses existing plan)
        # Round 2: tune
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Tuner 2",
                stderr="",
            ),
        )

        # Round 2: judge → DONE
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Judge 2\nJUDGE_DONE",
                stderr="",
            ),
        )

        thresholds = Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85)
        rc = orchestrate_tune(
            "test-rule",
            thresholds,
            rounds=2,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=commands_dir,
            runner=runner,
            rules_dir=str(rules_dir),
        )

        assert rc == 0
        # Round 1: design+tune+judge (3); Round 2 TUNE_MORE: tune+judge only (2) = 5
        assert len(runner.calls) == 5
        # Verify design was called in round 1 but NOT in round 2
        ralph_dirs = [call[0] for call in runner.calls]
        assert ralph_dirs[0] == design_dir  # round 1: design
        assert ralph_dirs[1] == tune_dir  # round 1: tune
        assert ralph_dirs[2] == judge_dir  # round 1: judge
        assert ralph_dirs[3] == tune_dir  # round 2: tune (no design)
        assert ralph_dirs[4] == judge_dir  # round 2: judge

    def test_orchestrate_tune_raise_updates_thresholds(self, tmp_path: Path) -> None:
        """JUDGE_RAISE updates thresholds for next round's ralph args."""
        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_tune,
        )

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        # Round 1: design
        def r1_design() -> None:
            state_dir = tmp_path / "commands" / "tune" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "test-rule.plan.md").write_text("# Plan\n")

        runner.add_response(
            r1_design,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design", stderr=""
            ),
        )

        # Round 1: tune
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Tune", stderr=""
            ),
        )

        # Round 1: judge → RAISE
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Analysis\nJUDGE_RAISE 0.97 0.88 0.92",
                stderr="",
            ),
        )

        # Round 2: design with new thresholds
        def r2_design() -> None:
            state_dir = tmp_path / "commands" / "tune" / "state"
            (state_dir / "test-rule.plan.md").write_text("# Plan 2\n")

        runner.add_response(
            r2_design,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design 2", stderr=""
            ),
        )

        # Round 2: tune
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Tune 2", stderr=""
            ),
        )

        # Round 2: judge → DONE
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Analysis 2\nJUDGE_DONE",
                stderr="",
            ),
        )

        thresholds = Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85)
        rc = orchestrate_tune(
            "test-rule",
            thresholds,
            rounds=2,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
        )

        assert rc == 0

        # Verify round 2 design was called with new thresholds
        design_call_r2 = runner.calls[3]  # index 3 is round 2, position 0 (design)
        assert "--p_min" in design_call_r2[1]
        p_min_idx = design_call_r2[1].index("--p_min")
        assert design_call_r2[1][p_min_idx + 1] == "0.97"

    def test_orchestrate_tune_abandon_disables_and_logs(self, tmp_path: Path) -> None:
        """JUDGE_ABANDON disables rule, logs reason, and exits cleanly."""
        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_tune,
        )

        runner = FakeRalphRunner()

        # Round 1: design
        def r1_design() -> None:
            state_dir = tmp_path / "commands" / "tune" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "test-rule.plan.md").write_text("# Plan\n")

        runner.add_response(
            r1_design,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design", stderr=""
            ),
        )

        # Round 1: tune
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Tune", stderr=""
            ),
        )

        # Round 1: judge → ABANDON
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Analysis\nJUDGE_ABANDON",
                stderr="",
            ),
        )

        # Setup rule file to be abandoned
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        thresholds = Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85)
        rc = orchestrate_tune(
            "test-rule",
            thresholds,
            rounds=1,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
        )

        assert rc == 0

        # Verify rule was disabled
        content = rule_file.read_text()
        assert "tier: disabled" in content

        # Verify log entry exists
        log_file = tmp_path / ".vaudeville" / "logs" / "abandoned.jsonl"
        assert log_file.exists()

    def test_orchestrate_tune_round_cap_exits_cleanly(self, tmp_path: Path) -> None:
        """Orchestrator exits after K rounds even if no DONE/ABANDON signal."""
        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_tune,
        )

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        # Round 1: design + tune + judge → CONTINUE_TUNE_MORE
        def design_side_effect() -> None:
            state_dir = tmp_path / "commands" / "tune" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "test-rule.plan.md").write_text("# Plan\n")

        runner.add_response(
            design_side_effect,
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
                args=["ralph"],
                returncode=0,
                stdout="Analysis\nJUDGE_CONTINUE_TUNE_MORE",
                stderr="",
            ),
        )

        # Round 2: TUNE_MORE → design skipped, tune + judge → CONTINUE_TUNE_MORE
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Tune 2", stderr=""
            ),
        )
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Analysis\nJUDGE_CONTINUE_TUNE_MORE",
                stderr="",
            ),
        )

        thresholds = Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85)
        rc = orchestrate_tune(
            "test-rule",
            thresholds,
            rounds=2,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
        )

        assert rc == 0
        # Round 1: design+tune+judge (3); Round 2 TUNE_MORE: tune+judge only (2) = 5
        assert len(runner.calls) == 5

    def test_orchestrate_tune_ralph_nonzero_raises_error(self, tmp_path: Path) -> None:
        """Ralph non-zero exit raises RalphError with phase name."""
        from vaudeville.orchestrator import (
            RalphError,
            Thresholds,
            orchestrate_tune,
        )

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        # Design fails
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=1,
                stdout="",
                stderr="ralph error",
            ),
        )

        thresholds = Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85)

        with pytest.raises(RalphError) as exc_info:
            orchestrate_tune(
                "test-rule",
                thresholds,
                rounds=1,
                tuner_iters=5,
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
                rules_dir=str(rules_dir),
            )

        assert "design" in str(exc_info.value).lower()

    def test_orchestrate_tune_passes_rules_dir_to_ralph_args(
        self, tmp_path: Path
    ) -> None:
        """--rules_dir is included in phase args passed to ralph."""
        from vaudeville.orchestrator import Thresholds, orchestrate_tune

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        def mk_empty_plan() -> None:
            state_dir = tmp_path / "commands" / "tune" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "test-rule.plan.md").write_text("EMPTY_PLAN\n")

        runner.add_response(
            mk_empty_plan,
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

        orchestrate_tune(
            "test-rule",
            Thresholds(0.95, 0.80, 0.85),
            rounds=1,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
        )

        design_args = runner.calls[0][1]
        assert "--rules_dir" in design_args
        idx = design_args.index("--rules_dir")
        assert design_args[idx + 1] == str(rules_dir)

    def test_orchestrate_tune_injects_env_var(self, tmp_path: Path) -> None:
        """VAUDEVILLE_RULES_DIR env var is set when ralph runner is invoked."""
        from vaudeville.orchestrator import Thresholds, orchestrate_tune

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        captured: dict[str, str | None] = {}

        def mk_plan_and_capture() -> None:
            captured["rules_dir"] = os.environ.get("VAUDEVILLE_RULES_DIR")
            state_dir = tmp_path / "commands" / "tune" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "test-rule.plan.md").write_text("EMPTY_PLAN\n")

        runner = FakeRalphRunner()
        runner.add_response(
            mk_plan_and_capture,
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

        orchestrate_tune(
            "test-rule",
            Thresholds(0.95, 0.80, 0.85),
            rounds=1,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
        )

        assert captured["rules_dir"] == str(rules_dir)
