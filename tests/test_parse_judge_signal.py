"""Tests for vaudeville.orchestrator.parse_judge_signal."""

from __future__ import annotations

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

    def test_parse_judge_raise_double_dot_float_raises_error(self) -> None:
        """JUDGE_RAISE with '1..0' matches regex but float() raises ValueError → JudgeParseError."""
        from vaudeville.orchestrator import JudgeParseError, parse_judge_signal

        output = "Analysis\nJUDGE_RAISE 1..0 0.5 0.5"

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
