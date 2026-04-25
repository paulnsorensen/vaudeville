"""Tests for vaudeville.orchestrator._default_prompt."""

from __future__ import annotations

import stat
from pathlib import Path


class TestBuildDefaultInstructions:
    def test_returns_analytics_directive_when_script_has_output(
        self, tmp_path: Path
    ) -> None:
        """When session-analytics.sh emits output, use ANALYTICS_DIRECTIVE."""
        from vaudeville.orchestrator._default_prompt import build_default_instructions

        script_dir = tmp_path / "commands" / "generate"
        script_dir.mkdir(parents=True)
        script = script_dir / "session-analytics.sh"
        script.write_text("#!/usr/bin/env bash\necho 'pattern: hedging detected'\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        result = build_default_instructions(str(tmp_path))

        assert "pattern: hedging detected" in result
        assert "Mine the patterns below" in result

    def test_returns_curated_bundle_when_script_missing(self, tmp_path: Path) -> None:
        """No session-analytics.sh → return curated bundle."""
        from vaudeville.orchestrator._default_prompt import (
            _CURATED_BUNDLE,
            build_default_instructions,
        )

        result = build_default_instructions(str(tmp_path))

        assert result == _CURATED_BUNDLE

    def test_returns_curated_bundle_when_script_produces_empty_output(
        self, tmp_path: Path
    ) -> None:
        """Script exists but outputs nothing → fall back to curated bundle."""
        from vaudeville.orchestrator._default_prompt import (
            _CURATED_BUNDLE,
            build_default_instructions,
        )

        script_dir = tmp_path / "commands" / "generate"
        script_dir.mkdir(parents=True)
        script = script_dir / "session-analytics.sh"
        script.write_text("#!/usr/bin/env bash\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        result = build_default_instructions(str(tmp_path))

        assert result == _CURATED_BUNDLE

    def test_run_session_analytics_returns_empty_on_exception(
        self, tmp_path: Path
    ) -> None:
        """subprocess.run raising OSError → empty string (fail-open)."""
        from unittest.mock import patch

        from vaudeville.orchestrator._default_prompt import _run_session_analytics

        script_dir = tmp_path / "commands" / "generate"
        script_dir.mkdir(parents=True)
        script = script_dir / "session-analytics.sh"
        script.write_text("#!/usr/bin/env bash\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        with patch(
            "vaudeville.orchestrator._default_prompt.subprocess.run",
            side_effect=OSError("boom"),
        ):
            result = _run_session_analytics(str(tmp_path))

        assert result == ""

    def test_analytics_directive_contains_analytics_placeholder(self) -> None:
        """_ANALYTICS_DIRECTIVE uses {analytics} format slot."""
        from vaudeville.orchestrator._default_prompt import _ANALYTICS_DIRECTIVE

        formatted = _ANALYTICS_DIRECTIVE.format(analytics="test_pattern")
        assert "test_pattern" in formatted

    def test_curated_bundle_covers_three_regressions(self) -> None:
        """Curated bundle mentions hedging, premature completion, sycophancy."""
        from vaudeville.orchestrator._default_prompt import _CURATED_BUNDLE

        assert "hedging" in _CURATED_BUNDLE
        assert (
            "premature completion" in _CURATED_BUNDLE.lower()
            or "premature" in _CURATED_BUNDLE
        )
        assert "sycophancy" in _CURATED_BUNDLE or "dismissal" in _CURATED_BUNDLE
