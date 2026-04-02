"""Tests for inference backends, setup.py, __main__.py, daemon extras, and client extras."""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from conftest import MockBackend


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- GGUFBackend ---


class TestGGUFBackend:
    def _make_mock_llama(self, content: str = "VERDICT: clean") -> MagicMock:
        mock_llm = MagicMock()
        mock_llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return mock_llm

    def test_classify_returns_string(self) -> None:
        mock_llm = self._make_mock_llama("VERDICT: violation\nREASON: test")
        mock_lm_cls = MagicMock(return_value=mock_llm)
        mock_hub = MagicMock(return_value="/tmp/fake-model.gguf")

        with patch.dict(
            "sys.modules",
            {
                "huggingface_hub": MagicMock(hf_hub_download=mock_hub),
                "llama_cpp": MagicMock(Llama=mock_lm_cls),
            },
        ):
            from vaudeville.server.gguf_backend import GGUFBackend

            backend = GGUFBackend()
            result = backend.classify("test prompt", max_tokens=50)

        assert result == "VERDICT: violation\nREASON: test"

    def test_classify_passes_max_tokens(self) -> None:
        mock_llm = self._make_mock_llama()
        mock_lm_cls = MagicMock(return_value=mock_llm)
        mock_hub = MagicMock(return_value="/tmp/fake.gguf")

        with patch.dict(
            "sys.modules",
            {
                "huggingface_hub": MagicMock(hf_hub_download=mock_hub),
                "llama_cpp": MagicMock(Llama=mock_lm_cls),
            },
        ):
            from vaudeville.server.gguf_backend import GGUFBackend

            backend = GGUFBackend()
            backend.classify("prompt", max_tokens=25)

        call_kwargs = mock_llm.create_chat_completion.call_args[1]
        assert call_kwargs["max_tokens"] == 25


# --- MLXBackend ---


class TestMLXBackend:
    def _make_mocks(self, output: str = "VERDICT: clean") -> tuple:
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_load = MagicMock(return_value=(mock_model, mock_tokenizer))
        mock_generate = MagicMock(return_value=output)
        return mock_model, mock_tokenizer, mock_load, mock_generate

    def _mlx_modules(
        self, mock_load: MagicMock, mock_generate: MagicMock
    ) -> dict[str, MagicMock]:
        """Build sys.modules dict that satisfies both mlx_lm and mlx_lm.generate imports."""
        mock_generate_step = MagicMock()
        mock_generate_mod = MagicMock(generate_step=mock_generate_step)
        mock_mlx = MagicMock(
            load=mock_load,
            stream_generate=mock_generate,
            generate=mock_generate_mod,
        )
        return {
            "mlx_lm": mock_mlx,
            "mlx_lm.generate": mock_generate_mod,
        }

    def test_classify_returns_generated_text(self) -> None:
        _, _, mock_load, mock_generate = self._make_mocks("VERDICT: clean")
        # stream_generate returns an iterable of response objects
        response_obj = MagicMock(text="VERDICT: clean", finish_reason="stop")
        mock_stream = MagicMock(return_value=iter([response_obj]))
        modules = self._mlx_modules(mock_load, mock_stream)
        with patch.dict("sys.modules", modules):
            from vaudeville.server.mlx_backend import MLXBackend

            backend = MLXBackend("test-model")
            result = backend.classify("test prompt")
        assert result == "VERDICT: clean"

    def test_apply_chat_template_with_tokenizer_method(self) -> None:
        _, mock_tokenizer, mock_load, _ = self._make_mocks()
        mock_tokenizer.apply_chat_template.return_value = "<formatted>"
        response_obj = MagicMock(text="VERDICT: clean", finish_reason="stop")
        mock_stream = MagicMock(return_value=iter([response_obj]))
        modules = self._mlx_modules(mock_load, mock_stream)
        with patch.dict("sys.modules", modules):
            from vaudeville.server.mlx_backend import MLXBackend

            backend = MLXBackend()
            backend.classify("my prompt")
        mock_tokenizer.apply_chat_template.assert_called_once()

    def test_apply_chat_template_fallback_when_no_method(self) -> None:
        _, mock_tokenizer, mock_load, _ = self._make_mocks()
        del mock_tokenizer.apply_chat_template  # remove method so hasattr returns False
        modules = self._mlx_modules(mock_load, MagicMock())
        with patch.dict("sys.modules", modules):
            from vaudeville.server.mlx_backend import MLXBackend

            backend = MLXBackend()
            formatted = backend._apply_chat_template("hello")
        assert "<|user|>" in formatted
        assert "hello" in formatted


# --- setup.py ---


class TestDetectPlatform:
    def test_darwin_arm64_returns_mlx(self) -> None:
        with patch("vaudeville.setup.platform") as mock_platform:
            mock_platform.system.return_value = "Darwin"
            mock_platform.machine.return_value = "arm64"
            from vaudeville.setup import _detect_platform

            assert _detect_platform() == "mlx"

    def test_linux_returns_gguf(self) -> None:
        with patch("vaudeville.setup.platform") as mock_platform:
            mock_platform.system.return_value = "Linux"
            mock_platform.machine.return_value = "x86_64"
            from vaudeville.setup import _detect_platform

            assert _detect_platform() == "gguf"


class TestSetupMLX:
    def test_calls_load_and_generate(self) -> None:
        mock_model = MagicMock()
        mock_tok = MagicMock()
        mock_load = MagicMock(return_value=(mock_model, mock_tok))
        mock_generate = MagicMock(return_value="ok")
        mock_mlx = MagicMock(load=mock_load, generate=mock_generate)
        with patch.dict("sys.modules", {"mlx_lm": mock_mlx}):
            from vaudeville.setup import _setup_mlx

            _setup_mlx()
        mock_load.assert_called_once()
        mock_generate.assert_called_once()


class TestSetupGGUF:
    def test_calls_download_and_inference(self) -> None:
        mock_hub = MagicMock(hf_hub_download=MagicMock(return_value="/tmp/m.gguf"))
        mock_llm = MagicMock()
        mock_llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }
        mock_llama_cls = MagicMock(return_value=mock_llm)
        mock_llama_cpp = MagicMock(Llama=mock_llama_cls)
        with patch.dict(
            "sys.modules",
            {"huggingface_hub": mock_hub, "llama_cpp": mock_llama_cpp},
        ):
            from vaudeville.setup import _setup_gguf

            _setup_gguf()
        mock_hub.hf_hub_download.assert_called_once()
        mock_llm.create_chat_completion.assert_called_once()


class TestSetupMain:
    def test_mlx_path_runs(self, capsys) -> None:
        with (
            patch("vaudeville.setup._detect_platform", return_value="mlx"),
            patch("vaudeville.setup._setup_mlx"),
        ):
            from vaudeville.setup import main

            main()
        assert "Setup complete" in capsys.readouterr().out

    def test_gguf_path_runs(self, capsys) -> None:
        with (
            patch("vaudeville.setup._detect_platform", return_value="gguf"),
            patch("vaudeville.setup._setup_gguf"),
        ):
            from vaudeville.setup import main

            main()
        assert "Setup complete" in capsys.readouterr().out

    def test_import_error_exits_1(self) -> None:
        with (
            patch("vaudeville.setup._detect_platform", return_value="mlx"),
            patch(
                "vaudeville.setup._setup_mlx",
                side_effect=ImportError("mlx_lm not found"),
            ),
        ):
            from vaudeville.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1


# --- __main__.py _init_backend ---


class TestInitBackend:
    def test_mlx_path(self) -> None:
        mock_mlx_cls = MagicMock(return_value=MagicMock())
        with patch("vaudeville.server.__main__.MLXBackend", mock_mlx_cls, create=True):
            from vaudeville.server.__main__ import _init_backend

            with patch.dict(
                "sys.modules",
                {
                    "vaudeville.server.mlx_backend": MagicMock(
                        MLXBackend=mock_mlx_cls, DEFAULT_MODEL="m"
                    )
                },
            ):
                backend = _init_backend("mlx", "my-model")
        assert backend is not None

    def test_gguf_path(self) -> None:
        mock_gguf_cls = MagicMock(return_value=MagicMock())
        with patch.dict(
            "sys.modules",
            {"vaudeville.server.gguf_backend": MagicMock(GGUFBackend=mock_gguf_cls)},
        ):
            from vaudeville.server.__main__ import _init_backend

            backend = _init_backend("gguf", None)
        assert backend is not None

    def test_unknown_backend_exits_1(self) -> None:
        from vaudeville.server.__main__ import _init_backend

        with pytest.raises(SystemExit) as exc_info:
            _init_backend("unknown", None)
        assert exc_info.value.code == 1


class TestServerMain:
    def test_main_starts_daemon(self) -> None:
        mock_backend = MockBackend()
        mock_daemon = MagicMock()
        mock_daemon_cls = MagicMock(return_value=mock_daemon)

        with (
            patch(
                "sys.argv",
                [
                    "__main__",
                    "--socket",
                    "/tmp/test-vd.sock",
                    "--pid-file",
                    "/tmp/test-vd.pid",
                    "--backend",
                    "mlx",
                ],
            ),
            patch(
                "vaudeville.server.__main__._init_backend", return_value=mock_backend
            ),
            patch("vaudeville.server.daemon.VaudevilleDaemon", mock_daemon_cls),
        ):
            from vaudeville.server.__main__ import main

            main()
        mock_daemon.serve.assert_called_once()

    def test_main_auto_detects_backend(self) -> None:
        mock_backend = MockBackend()
        mock_daemon = MagicMock()

        with (
            patch(
                "sys.argv",
                [
                    "__main__",
                    "--socket",
                    "/tmp/test-vd2.sock",
                    "--pid-file",
                    "/tmp/test-vd2.pid",
                    "--backend",
                    "auto",
                ],
            ),
            patch(
                "vaudeville.server.__main__.detect_backend",
                return_value="mlx",
            ),
            patch(
                "vaudeville.server.__main__._init_backend", return_value=mock_backend
            ),
            patch(
                "vaudeville.server.daemon.VaudevilleDaemon",
                MagicMock(return_value=mock_daemon),
            ),
        ):
            from vaudeville.server.__main__ import main

            main()
        mock_daemon.serve.assert_called_once()


# --- VaudevilleClient socket paths ---


class TestClientSocket:
    def test_classify_over_real_socket(self) -> None:
        """Test the actual socket read/write path using a real Unix socket."""
        import json as _json

        with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
            sock_path = f.name
        os.unlink(sock_path)

        response = {"verdict": "clean", "reason": "test ok", "action": "block"}
        server_done = threading.Event()

        def _serve() -> None:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(sock_path)
            srv.listen(1)
            srv.settimeout(3.0)
            conn, _ = srv.accept()
            data = b""
            while b"\n" not in data:
                data += conn.recv(4096)
            conn.sendall((_json.dumps(response) + "\n").encode())
            conn.close()
            srv.close()
            server_done.set()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        time.sleep(0.05)

        from vaudeville.core.client import VaudevilleClient

        # patch SOCKET_PATH so client uses our test socket
        with patch("vaudeville.core.client.SOCKET_PATH", sock_path):
            client = VaudevilleClient()
            result = client.classify("test-rule", {"text": "hello"})

        server_done.wait(timeout=3.0)
        assert result is not None
        assert result.verdict == "clean"
        assert result.reason == "test ok"


# --- Daemon extra coverage ---


class TestDaemonExtras:
    def test_cleanup_tolerates_missing_files(self) -> None:
        from vaudeville.server.daemon import VaudevilleDaemon

        daemon = VaudevilleDaemon(
            socket_path="/tmp/nonexistent-vd.sock",
            pid_file="/tmp/nonexistent-vd.pid",
            plugin_root=PROJECT_ROOT,
            backend=MockBackend(),
        )
        daemon._cleanup()  # should not raise

    def test_find_project_root_in_daemon(self) -> None:
        from vaudeville.server.daemon import _find_project_root

        result = _find_project_root()
        # Running inside a git repo — should return the project root
        assert result is not None
        assert os.path.isdir(result)

    def test_find_project_root_oserror(self) -> None:

        from vaudeville.server.daemon import _find_project_root

        with patch("vaudeville.server.daemon.subprocess.run", side_effect=OSError):
            assert _find_project_root() is None

    def test_find_project_root_timeout(self) -> None:
        import subprocess

        from vaudeville.server.daemon import _find_project_root

        with patch(
            "vaudeville.server.daemon.subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 5),
        ):
            assert _find_project_root() is None

    def test_accept_loop_stops_on_stop_event(self) -> None:
        from vaudeville.server.daemon import VaudevilleDaemon

        with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
            sock_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".pid", dir="/tmp", delete=False) as f:
            pid_path = f.name
        os.unlink(sock_path)

        daemon = VaudevilleDaemon(sock_path, pid_path, PROJECT_ROOT, MockBackend())
        mock_server = MagicMock()
        mock_server.accept.side_effect = socket.timeout
        daemon._stop_event.set()
        daemon._accept_loop(mock_server)  # should return immediately

    def test_handle_client_exception_logged(self) -> None:
        from vaudeville.server.daemon import VaudevilleDaemon

        daemon = VaudevilleDaemon(
            "/tmp/x.sock", "/tmp/x.pid", PROJECT_ROOT, MockBackend()
        )
        broken_conn = MagicMock()
        broken_conn.recv.side_effect = OSError("connection reset")
        daemon._handle_client(broken_conn)  # should not raise
        broken_conn.close.assert_called_once()

    def test_scan_dir_mtime_oserror(self) -> None:
        from vaudeville.server.daemon import _scan_dir_mtime

        result = _scan_dir_mtime("/nonexistent/path")
        assert result == 0.0
