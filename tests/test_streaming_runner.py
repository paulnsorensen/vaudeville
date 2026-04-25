"""Tests for streaming default_ralph_runner with on_line callback."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch


class TestDefaultRalphRunnerStreaming:
    def _make_mock_proc(
        self, stdout: str, stderr: str = "", returncode: int = 0
    ) -> MagicMock:
        mock = MagicMock()
        mock.stdout = io.StringIO(stdout)
        mock.stderr = io.StringIO(stderr)
        mock.returncode = returncode
        mock.wait.return_value = None
        return mock

    def test_on_line_fires_per_stdout_line(self) -> None:
        """on_line is called once per stdout line during execution."""
        from vaudeville.orchestrator._phase import default_ralph_runner

        lines: list[str] = []
        mock_proc = self._make_mock_proc("line1\nline2\nline3\n")

        with patch(
            "vaudeville.orchestrator._phase.subprocess.Popen", return_value=mock_proc
        ):
            default_ralph_runner("/dir", [], "/proj", on_line=lines.append)

        assert lines == ["line1", "line2", "line3"]

    def test_full_stdout_still_parseable_from_result(self) -> None:
        """CompletedProcess.stdout contains all lines joined."""
        from vaudeville.orchestrator._phase import default_ralph_runner

        mock_proc = self._make_mock_proc("alpha\nbeta\n")

        with patch(
            "vaudeville.orchestrator._phase.subprocess.Popen", return_value=mock_proc
        ):
            result = default_ralph_runner("/dir", [], "/proj", on_line=lambda _: None)

        assert "alpha" in result.stdout
        assert "beta" in result.stdout

    def test_returncode_propagated(self) -> None:
        """Non-zero returncode from process is preserved in result."""
        from vaudeville.orchestrator._phase import default_ralph_runner

        mock_proc = self._make_mock_proc("", returncode=3)

        with patch(
            "vaudeville.orchestrator._phase.subprocess.Popen", return_value=mock_proc
        ):
            result = default_ralph_runner("/dir", [], "/proj", on_line=lambda _: None)

        assert result.returncode == 3

    def test_without_on_line_uses_subprocess_run(self) -> None:
        """Without on_line, falls back to subprocess.run (not Popen)."""
        from vaudeville.orchestrator._phase import default_ralph_runner

        with (
            patch("vaudeville.orchestrator._phase.subprocess.run") as mock_run,
            patch("vaudeville.orchestrator._phase.subprocess.Popen") as mock_popen,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            default_ralph_runner("/dir", [], "/proj")

        mock_run.assert_called_once()
        mock_popen.assert_not_called()

    def test_stderr_captured_separately(self) -> None:
        """stderr lines are captured in result.stderr, not mixed into stdout."""
        from vaudeville.orchestrator._phase import default_ralph_runner

        mock_proc = self._make_mock_proc("out line\n", stderr="err line\n")

        with patch(
            "vaudeville.orchestrator._phase.subprocess.Popen", return_value=mock_proc
        ):
            result = default_ralph_runner("/dir", [], "/proj", on_line=lambda _: None)

        assert "out line" in result.stdout
        assert "err line" in result.stderr
        assert "err line" not in result.stdout

    def test_on_line_not_called_for_stderr(self) -> None:
        """stderr lines do NOT fire on_line (stdout only)."""
        from vaudeville.orchestrator._phase import default_ralph_runner

        lines: list[str] = []
        mock_proc = self._make_mock_proc("stdout line\n", stderr="stderr line\n")

        with patch(
            "vaudeville.orchestrator._phase.subprocess.Popen", return_value=mock_proc
        ):
            default_ralph_runner("/dir", [], "/proj", on_line=lines.append)

        assert "stdout line" in lines
        assert "stderr line" not in lines

    def test_none_stdout_stderr_handles_gracefully(self) -> None:
        """When proc.stdout and proc.stderr are None, result is still returned."""
        from vaudeville.orchestrator._phase import default_ralph_runner

        mock_proc = MagicMock()
        mock_proc.stdout = None
        mock_proc.stderr = None
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None

        with patch(
            "vaudeville.orchestrator._phase.subprocess.Popen", return_value=mock_proc
        ):
            result = default_ralph_runner("/dir", [], "/proj", on_line=lambda _: None)

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""


class TestMakeRunner:
    def test_none_on_line_returns_runner_unchanged(self) -> None:
        """_make_runner with on_line=None returns the same runner object."""
        from vaudeville.orchestrator._phase import _make_runner, default_ralph_runner

        result = _make_runner(default_ralph_runner, None)
        assert result is default_ralph_runner

    def test_default_runner_with_on_line_uses_streaming(self) -> None:
        """_make_runner wrapping default_ralph_runner uses real-time streaming."""
        from vaudeville.orchestrator._phase import _make_runner, default_ralph_runner

        lines: list[str] = []
        mock_proc = MagicMock()
        mock_proc.stdout = io.StringIO("streamed line\n")
        mock_proc.stderr = io.StringIO("")
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None

        wrapped = _make_runner(default_ralph_runner, lines.append)

        with patch(
            "vaudeville.orchestrator._phase.subprocess.Popen", return_value=mock_proc
        ):
            wrapped("/dir", [], "/proj")

        assert "streamed line" in lines

    def test_custom_runner_with_on_line_uses_posthoc(self) -> None:
        """_make_runner wrapping a custom runner calls on_line post-hoc per line."""
        import subprocess
        from vaudeville.orchestrator._phase import _make_runner

        def fake_runner(
            _ralph_dir: str, _extra_args: list[str], _project_root: str
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["ralph"], returncode=0, stdout="line A\nline B\n", stderr=""
            )

        lines: list[str] = []
        wrapped = _make_runner(fake_runner, lines.append)
        wrapped("/dir", [], "/proj")

        assert "line A" in lines
        assert "line B" in lines
