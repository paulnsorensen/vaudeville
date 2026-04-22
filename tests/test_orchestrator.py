"""Tests for the vaudeville orchestrator module.

Covers signal parsing, rule abandonment, tune/generate orchestration, and CLI rewiring.
Uses mock-ralph pattern to avoid real claude/ralph calls.
"""

from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import pytest


class TestParseJudgeSignal:
    """Test the judge signal parser against all signal types and malformed inputs."""

    def test_parse_judge_done(self) -> None:
        """JUDGE_DONE signal is recognized and parsed."""
        from vaudeville.orchestrator import parse_judge_signal

        output = "Some analysis here\nJUDGE_DONE"
        verdict = parse_judge_signal(output)

        assert verdict.kind == "JUDGE_DONE"
        assert verdict.raised is None
        assert verdict.raw_line == "JUDGE_DONE"

    def test_parse_judge_abandon(self) -> None:
        """JUDGE_ABANDON signal is recognized."""
        from vaudeville.orchestrator import parse_judge_signal

        output = "Analysis\nJUDGE_ABANDON"
        verdict = parse_judge_signal(output)

        assert verdict.kind == "JUDGE_ABANDON"
        assert verdict.raised is None

    def test_parse_judge_continue_re_design(self) -> None:
        """JUDGE_CONTINUE_RE_DESIGN signal is recognized."""
        from vaudeville.orchestrator import parse_judge_signal

        output = "Analysis\nJUDGE_CONTINUE_RE_DESIGN"
        verdict = parse_judge_signal(output)

        assert verdict.kind == "JUDGE_CONTINUE_RE_DESIGN"

    def test_parse_judge_continue_tune_more(self) -> None:
        """JUDGE_CONTINUE_TUNE_MORE signal is recognized."""
        from vaudeville.orchestrator import parse_judge_signal

        output = "Analysis\nJUDGE_CONTINUE_TUNE_MORE"
        verdict = parse_judge_signal(output)

        assert verdict.kind == "JUDGE_CONTINUE_TUNE_MORE"

    def test_parse_judge_continue_keep_state(self) -> None:
        """JUDGE_CONTINUE_KEEP_STATE signal is recognized."""
        from vaudeville.orchestrator import parse_judge_signal

        output = "Analysis\nJUDGE_CONTINUE_KEEP_STATE"
        verdict = parse_judge_signal(output)

        assert verdict.kind == "JUDGE_CONTINUE_KEEP_STATE"

    def test_parse_judge_raise_with_floats(self) -> None:
        """JUDGE_RAISE with three float thresholds is parsed."""
        from vaudeville.orchestrator import Thresholds, parse_judge_signal

        output = "Analysis\nJUDGE_RAISE 0.97 0.88 0.92"
        verdict = parse_judge_signal(output)

        assert verdict.kind == "JUDGE_RAISE"
        assert isinstance(verdict.raised, Thresholds)
        assert verdict.raised.p_min == 0.97
        assert verdict.raised.r_min == 0.88
        assert verdict.raised.f1_min == 0.92

    def test_parse_judge_raise_malformed_raises_error(self) -> None:
        """JUDGE_RAISE with malformed floats raises JudgeParseError."""
        from vaudeville.orchestrator import JudgeParseError, parse_judge_signal

        output = "Analysis\nJUDGE_RAISE 0.97 bad 0.92"

        with pytest.raises(JudgeParseError):
            parse_judge_signal(output)

    def test_parse_no_signal_raises_error(self) -> None:
        """Output with no signal line raises JudgeParseError."""
        from vaudeville.orchestrator import JudgeParseError, parse_judge_signal

        output = "Just some analysis, no signal"

        with pytest.raises(JudgeParseError):
            parse_judge_signal(output)

    def test_parse_signal_with_trailing_whitespace(self) -> None:
        """Signal line is found even if it's not the final line (bottom-up search)."""
        from vaudeville.orchestrator import parse_judge_signal

        output = "Analysis\nJUDGE_DONE\nTrailing junk\nMore junk"
        verdict = parse_judge_signal(output)

        assert verdict.kind == "JUDGE_DONE"

    def test_parse_signal_on_last_line_of_many(self) -> None:
        """Signal is correctly extracted from last non-empty line after strip."""
        from vaudeville.orchestrator import parse_judge_signal

        output = "Line 1\nLine 2\nLine 3\nJUDGE_CONTINUE_TUNE_MORE  \n  "
        verdict = parse_judge_signal(output)

        assert verdict.kind == "JUDGE_CONTINUE_TUNE_MORE"
        assert verdict.raw_line == "JUDGE_CONTINUE_TUNE_MORE"


class TestAbandonRule:
    """Test rule abandonment: tier update, file search, reason logging."""

    def test_abandon_sets_tier_disabled_in_project_path(self, tmp_path: Path) -> None:
        """Abandon updates tier to disabled in project .vaudeville/rules/ path."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        abandon_rule("test-rule", "test reason", {}, str(tmp_path))

        content = rule_file.read_text()
        assert "tier: disabled" in content
        assert "ABANDONED" in content

    def test_abandon_fallback_to_user_path(self, tmp_path: Path) -> None:
        """Abandon falls back to ~/.vaudeville/rules/ if project path missing."""
        from vaudeville.orchestrator import abandon_rule

        # Mock user home path — rules live under ~/.vaudeville/rules/
        user_home = tmp_path / "user_rules"
        user_home.mkdir()
        rule_dir = user_home / ".vaudeville" / "rules"
        rule_dir.mkdir(parents=True)
        rule_file = rule_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        with patch("os.path.expanduser") as mock_expand:
            mock_expand.side_effect = lambda p: str(user_home) if p == "~" else p
            project_path = tmp_path / "project"
            project_path.mkdir()

            abandon_rule("test-rule", "test reason", {}, str(project_path))

        content = rule_file.read_text()
        assert "tier: disabled" in content

    def test_abandon_appends_reason_comment(self, tmp_path: Path) -> None:
        """Abandon appends ISO UTC timestamp and reason as YAML comment."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        abandon_rule("test-rule", "rule is impossible", {}, str(tmp_path))

        content = rule_file.read_text()
        assert "# ABANDONED" in content
        assert "rule is impossible" in content

    def test_abandon_flattens_newlines_in_reason(self, tmp_path: Path) -> None:
        """Abandon converts newlines in reason to spaces."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        reason = "first line\nsecond line\nthird line"
        abandon_rule("test-rule", reason, {}, str(tmp_path))

        content = rule_file.read_text()
        assert "first line second line third line" in content

    def test_abandon_writes_jsonl_log(self, tmp_path: Path) -> None:
        """Abandon appends one JSON line to .vaudeville/logs/abandoned.jsonl."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        metrics: dict[str, object] = {"precision": 0.8, "recall": 0.75}
        abandon_rule("test-rule", "stagnant", metrics, str(tmp_path))

        log_file = tmp_path / ".vaudeville" / "logs" / "abandoned.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) >= 1

        entry = json.loads(lines[-1])
        assert entry["rule"] == "test-rule"
        assert entry["reason"] == "stagnant"
        assert entry["metrics"] == metrics

    def test_abandon_creates_log_dir_if_missing(self, tmp_path: Path) -> None:
        """Abandon creates .vaudeville/logs/ if it doesn't exist."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        abandon_rule("test-rule", "test", {}, str(tmp_path))

        log_dir = tmp_path / ".vaudeville" / "logs"
        assert log_dir.exists()

    def test_abandon_missing_rule_file_raises_filenotfound(
        self, tmp_path: Path
    ) -> None:
        """Abandon raises FileNotFoundError if rule file not found in any path."""
        from vaudeville.orchestrator import abandon_rule

        project_path = tmp_path / "project"
        project_path.mkdir()

        with patch("os.path.expanduser") as mock_expand:
            mock_expand.return_value = str(tmp_path / "user_rules")

            with pytest.raises(FileNotFoundError):
                abandon_rule("nonexistent-rule", "test", {}, str(project_path))


class FakeRalphRunner:
    """Mock ralph runner that returns scripted responses and records calls."""

    def __init__(self) -> None:
        self.responses: list[
            tuple[Callable[[], None] | None, subprocess.CompletedProcess[str]]
        ] = []
        self.calls: list[tuple[str, list[str], str]] = []

    def add_response(
        self,
        side_effect_fn: Callable[[], None] | None,
        completed_process: subprocess.CompletedProcess[str],
    ) -> None:
        """Queue a response: optional side effect + CompletedProcess to return."""
        self.responses.append((side_effect_fn, completed_process))

    def __call__(
        self, ralph_dir: str, extra_args: list[str], project_root: str
    ) -> subprocess.CompletedProcess[str]:
        """Called by orchestrator; pops first queued response."""
        self.calls.append((ralph_dir, extra_args, project_root))

        if not self.responses:
            return subprocess.CompletedProcess(
                args=["ralph", "run", ralph_dir],
                returncode=1,
                stdout="",
                stderr="No more mock responses queued",
            )

        side_effect_fn, completed_process = self.responses.pop(0)
        if side_effect_fn:
            side_effect_fn()

        return completed_process


class TestOrchestrateTune:
    """Test the tune orchestration loop: round1 DONE, multi-round CONTINUE, RAISE, ABANDON."""

    def test_orchestrate_tune_round1_done(self, tmp_path: Path) -> None:
        """Round 1: design → tune → judge returns JUDGE_DONE → exit cleanly."""
        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_tune,
        )

        runner = FakeRalphRunner()

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
        )

        assert rc == 0

        # Verify round 2 design was called with new thresholds
        # Check that second design call has --p_min 0.97, --r_min 0.88, --f1_min 0.92
        # Design is 3rd call (0-indexed: 2), 4th call is tune (3), 5th is judge (4)
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
            )

        assert "design" in str(exc_info.value).lower()


class TestOrchestrateGenerate:
    """Test generate orchestration: designer → eval rules → tune if needed."""

    def test_orchestrate_generate_all_rules_pass_immediately(
        self, tmp_path: Path
    ) -> None:
        """All 3 rules pass eval immediately → no tune pipeline → exit 0."""
        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_generate,
        )

        runner = FakeRalphRunner()

        # Generate phase: writes 3 rule YAMLs
        def generate_side_effect() -> None:
            rules_dir = tmp_path / ".vaudeville" / "rules"
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
            "vaudeville.orchestrator._eval_rule",
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
            )

        assert rc == 0

    def test_orchestrate_generate_one_rule_tunes(self, tmp_path: Path) -> None:
        """One rule fails eval → enters tune pipeline → completes."""
        from vaudeville.orchestrator import (
            Thresholds,
            orchestrate_generate,
        )

        runner = FakeRalphRunner()

        # Generate: writes 3 rules
        def generate_side_effect() -> None:
            rules_dir = tmp_path / ".vaudeville" / "rules"
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
                stdout="Judge\nJUDGE_DONE",
                stderr="",
            ),
        )

        with patch("vaudeville.orchestrator._eval_rule", side_effect=mock_eval):
            rc = orchestrate_generate(
                instructions="test instructions",
                thresholds=thresholds,
                rounds=1,
                tuner_iters=5,
                mode="shadow",
                project_root=str(tmp_path),
                commands_dir=str(tmp_path / "commands"),
                runner=runner,
            )

        assert rc == 0

    def test_eval_rule_parses_metrics_from_stdout(self) -> None:
        """_eval_rule parses precision/recall/f1 regex matches from subprocess stdout."""
        from vaudeville.orchestrator import _eval_rule

        fake = subprocess.CompletedProcess(
            args=["uv"],
            returncode=0,
            stdout="metrics: precision=0.92 recall=0.81 f1=0.86",
            stderr="",
        )
        with patch("subprocess.run", return_value=fake):
            result = _eval_rule("rule", "/proj")
        assert result is not None
        assert result.p_min == 0.92
        assert result.r_min == 0.81
        assert result.f1_min == 0.86

    def test_eval_rule_returns_none_when_metrics_missing(self) -> None:
        """Missing precision/recall/f1 tokens → None (no crash)."""
        from vaudeville.orchestrator import _eval_rule

        fake = subprocess.CompletedProcess(
            args=["uv"], returncode=1, stdout="no metrics here", stderr="boom"
        )
        with patch("subprocess.run", return_value=fake):
            assert _eval_rule("rule", "/proj") is None

    def test_eval_rule_returns_none_when_uv_missing(self) -> None:
        """FileNotFoundError from subprocess.run → None (graceful degrade)."""
        from vaudeville.orchestrator import _eval_rule

        with patch("subprocess.run", side_effect=FileNotFoundError("uv not found")):
            assert _eval_rule("rule", "/proj") is None

    def test_extract_abandon_reason_strips_signal_line(self) -> None:
        """_extract_abandon_reason returns prose above JUDGE_* signal line."""
        from vaudeville.orchestrator import _extract_abandon_reason

        stdout = "analysis line one\nanalysis line two\nJUDGE_ABANDON"
        reason = _extract_abandon_reason(stdout)
        assert reason == "analysis line one\nanalysis line two"

    def test_extract_abandon_reason_empty_when_signal_first(self) -> None:
        """Judge output starting with signal → empty reason."""
        from vaudeville.orchestrator import _extract_abandon_reason

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
            )


class TestCmdRewire:
    """Test __main__.py cmd_tune and cmd_generate call the orchestrator correctly."""

    def test_cmd_tune_forwards_rounds_and_tuner_iters(self) -> None:
        """cmd_tune passes --rounds and --tuner-iters to orchestrator."""
        from vaudeville.__main__ import cmd_tune

        with patch("vaudeville.orchestrator.orchestrate_tune") as mock_orch:
            mock_orch.return_value = 0

            args = Namespace(
                rule="test-rule",
                p_min=0.95,
                r_min=0.80,
                f1_min=0.85,
                rounds=2,
                tuner_iters=10,
            )

            with patch("vaudeville.__main__._find_project_root", return_value="/proj"):
                with patch(
                    "vaudeville.__main__._find_commands_dir",
                    return_value="/proj/commands",
                ):
                    cmd_tune(args)

        # Verify orchestrate_tune was called with rounds=2, tuner_iters=10
        assert mock_orch.called
        call_kwargs = mock_orch.call_args[1]
        assert call_kwargs["rounds"] == 2
        assert call_kwargs["tuner_iters"] == 10

    def test_cmd_generate_forwards_mode(self) -> None:
        """cmd_generate forwards --live flag as mode to orchestrator."""
        from vaudeville.__main__ import cmd_generate

        with patch("vaudeville.orchestrator.orchestrate_generate") as mock_orch:
            mock_orch.return_value = 0

            args = Namespace(
                instructions="test instructions",
                p_min=0.95,
                r_min=0.80,
                f1_min=0.85,
                live=True,
                rounds=3,
                tuner_iters=10,
            )

            with patch("vaudeville.__main__._find_project_root", return_value="/proj"):
                with patch(
                    "vaudeville.__main__._find_commands_dir",
                    return_value="/proj/commands",
                ):
                    cmd_generate(args)

        assert mock_orch.called
        call_kwargs = mock_orch.call_args[1]
        assert call_kwargs["mode"] == "live"
