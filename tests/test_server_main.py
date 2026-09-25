"""Tests for `vaudeville.server.__main__` argument parsing and startup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestServerMain:
    def test_main_starts_daemon(self) -> None:
        mock_daemon = MagicMock()
        mock_daemon_cls = MagicMock(return_value=mock_daemon)
        mock_pid_fd = MagicMock()

        with (
            patch(
                "sys.argv",
                [
                    "__main__",
                    "--socket",
                    "/tmp/test-vd.sock",
                    "--pid-file",
                    "/tmp/test-vd.pid",
                ],
            ),
            patch(
                "vaudeville.server.daemon.acquire_pid_lock",
                return_value=mock_pid_fd,
            ),
            patch("vaudeville.server.daemon.VaudevilleDaemon", mock_daemon_cls),
            patch("vaudeville.server.event_log.EventLogger"),
        ):
            from vaudeville.server.__main__ import main

            main()
        mock_daemon.serve.assert_called_once()
        mock_daemon_cls.assert_called_once()
        _, kwargs = mock_daemon_cls.call_args
        assert kwargs["pid_fd"] is mock_pid_fd

    def test_main_exits_quietly_when_pid_lock_unavailable(self) -> None:
        """A second instance finds the PID lock held and never starts a daemon."""
        mock_daemon_cls = MagicMock()

        with (
            patch(
                "sys.argv",
                [
                    "__main__",
                    "--socket",
                    "/tmp/test-vd3.sock",
                    "--pid-file",
                    "/tmp/test-vd3.pid",
                ],
            ),
            patch(
                "vaudeville.server.daemon.acquire_pid_lock",
                return_value=None,
            ),
            patch("vaudeville.server.daemon.VaudevilleDaemon", mock_daemon_cls),
        ):
            from vaudeville.server.__main__ import main

            main()
        mock_daemon_cls.assert_not_called()
