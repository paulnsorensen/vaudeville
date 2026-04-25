"""Tests for deterministic exit conditions in orchestrate_tune."""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import FakeRalphRunner


class TestTuneExitPromise:
    def test_tuner_promise_exits_after_one_judge_call(self, tmp_path: Path) -> None:
        """Tuner emits THRESHOLDS_MET promise → loop exits after judge (non-RAISE)."""
        from vaudeville.orchestrator import Thresholds, orchestrate_tune

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        def mk_plan() -> None:
            state = tmp_path / "commands" / "tune" / "state"
            state.mkdir(parents=True, exist_ok=True)
            (state / "test-rule.plan.md").write_text("# Plan\n1. Fix something\n")

        # Round 1: design
        runner.add_response(
            mk_plan,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design", stderr=""
            ),
        )
        # Round 1: tune — emits the promise
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Tuning...\n<promise>THRESHOLDS_MET</promise>",
                stderr="",
            ),
        )
        # Round 1: judge — returns CONTINUE (promise should override this)
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Judge says\nJUDGE_CONTINUE_TUNE_MORE",
                stderr="",
            ),
        )

        rc = orchestrate_tune(
            "test-rule",
            Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85),
            rounds=5,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
        )

        assert rc == 0
        # design + tune + judge = 3 calls, loop exits after round 1
        assert len(runner.calls) == 3

    def test_judge_raise_overrides_promise(self, tmp_path: Path) -> None:
        """JUDGE_RAISE after promise → loop continues (RAISE overrides promise)."""
        from vaudeville.orchestrator import Thresholds, orchestrate_tune

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        def mk_plan() -> None:
            state = tmp_path / "commands" / "tune" / "state"
            state.mkdir(parents=True, exist_ok=True)
            (state / "test-rule.plan.md").write_text("# Plan\n1. Fix something\n")

        # Round 1: design
        runner.add_response(
            mk_plan,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design", stderr=""
            ),
        )
        # Round 1: tune — emits promise
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="<promise>THRESHOLDS_MET</promise>",
                stderr="",
            ),
        )
        # Round 1: judge — JUDGE_RAISE (overrides promise)
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Analysis\nJUDGE_RAISE 0.97 0.85 0.90",
                stderr="",
            ),
        )

        def mk_plan2() -> None:
            state = tmp_path / "commands" / "tune" / "state"
            (state / "test-rule.plan.md").write_text("# Plan2\n")

        # Round 2: design (triggered by JUDGE_RAISE)
        runner.add_response(
            mk_plan2,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design2", stderr=""
            ),
        )
        # Round 2: tune
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Tune2", stderr=""
            ),
        )
        # Round 2: judge — DONE
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Judge2\nJUDGE_DONE", stderr=""
            ),
        )

        rc = orchestrate_tune(
            "test-rule",
            Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85),
            rounds=5,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
        )

        assert rc == 0
        # 6 calls: round1 (design+tune+judge) + round2 (design+tune+judge)
        assert len(runner.calls) == 6


class TestTuneExitEmptyPlan:
    def test_empty_plan_with_continue_exits(self, tmp_path: Path) -> None:
        """EMPTY_PLAN + JUDGE_CONTINUE_RE_DESIGN → loop exits (runaway guard)."""
        from vaudeville.orchestrator import Thresholds, orchestrate_tune

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        def mk_empty_plan() -> None:
            state = tmp_path / "commands" / "tune" / "state"
            state.mkdir(parents=True, exist_ok=True)
            (state / "test-rule.plan.md").write_text("EMPTY_PLAN\n")

        # Round 1: design → writes EMPTY_PLAN
        runner.add_response(
            mk_empty_plan,
            subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="Design", stderr=""
            ),
        )
        # Tune is skipped (EMPTY_PLAN), judge runs
        runner.add_response(
            None,
            subprocess.CompletedProcess(
                args=["ralph"],
                returncode=0,
                stdout="Judge\nJUDGE_CONTINUE_RE_DESIGN",
                stderr="",
            ),
        )

        rc = orchestrate_tune(
            "test-rule",
            Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85),
            rounds=5,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
        )

        assert rc == 0
        # design + judge = 2 calls, exits after round 1 (no runaway)
        assert len(runner.calls) == 2


class TestTuneExitBackwardCompat:
    def test_existing_done_still_exits(self, tmp_path: Path) -> None:
        """JUDGE_DONE still causes exit (existing behavior preserved)."""
        from vaudeville.orchestrator import Thresholds, orchestrate_tune

        runner = FakeRalphRunner()
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)

        def mk_plan() -> None:
            state = tmp_path / "commands" / "tune" / "state"
            state.mkdir(parents=True, exist_ok=True)
            (state / "test-rule.plan.md").write_text("EMPTY_PLAN\n")

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

        rc = orchestrate_tune(
            "test-rule",
            Thresholds(p_min=0.95, r_min=0.80, f1_min=0.85),
            rounds=5,
            tuner_iters=5,
            project_root=str(tmp_path),
            commands_dir=str(tmp_path / "commands"),
            runner=runner,
            rules_dir=str(rules_dir),
        )

        assert rc == 0
        assert len(runner.calls) == 2
