"""Tests for inference backends, setup.py, __main__.py, daemon extras, and client extras."""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
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
                "llama_cpp.llama_cache": MagicMock(),
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
                "llama_cpp.llama_cache": MagicMock(),
            },
        ):
            from vaudeville.server.gguf_backend import GGUFBackend

            backend = GGUFBackend()
            backend.classify("prompt", max_tokens=25)

        call_kwargs = mock_llm.create_chat_completion.call_args[1]
        assert call_kwargs["max_tokens"] == 25


# --- MLXBackend ---


class TestMLXBackend:
    def _make_mocks(self, output: str = "VERDICT: clean") -> tuple[Any, Any, Any, Any]:
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
        assert "<|im_start|>user" in formatted
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


class TestCheckDiskSpace:
    def test_sufficient_space_passes(self, tmp_path: Path) -> None:
        cache_dir = str(tmp_path / ".cache" / "huggingface")
        mock_usage = MagicMock(free=10_000_000_000)
        with (
            patch("vaudeville.setup.os.path.expanduser", return_value=cache_dir),
            patch("vaudeville.setup.shutil.disk_usage", return_value=mock_usage),
        ):
            from vaudeville.setup import _check_disk_space

            _check_disk_space()  # should not raise

    def test_insufficient_space_exits_1(self, tmp_path: Path) -> None:
        cache_dir = str(tmp_path / ".cache" / "huggingface")
        mock_usage = MagicMock(free=1_000_000_000)
        with (
            patch("vaudeville.setup.os.path.expanduser", return_value=cache_dir),
            patch("vaudeville.setup.shutil.disk_usage", return_value=mock_usage),
        ):
            from vaudeville.setup import _check_disk_space

            with pytest.raises(SystemExit) as exc_info:
                _check_disk_space()
        assert exc_info.value.code == 1

    def test_creates_cache_dir_if_missing(self, tmp_path: Path) -> None:
        cache_dir = str(tmp_path / ".cache" / "huggingface")
        mock_usage = MagicMock(free=10_000_000_000)
        with (
            patch("vaudeville.setup.os.path.expanduser", return_value=cache_dir),
            patch("vaudeville.setup.shutil.disk_usage", return_value=mock_usage),
        ):
            from vaudeville.setup import _check_disk_space

            _check_disk_space()
        assert os.path.isdir(cache_dir)


class TestEnableHfTransfer:
    def test_sets_env_var_when_package_available(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "HF_HUB_ENABLE_HF_TRANSFER"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch.dict("sys.modules", {"hf_transfer": MagicMock()}),
        ):
            from vaudeville.setup import _enable_hf_transfer

            _enable_hf_transfer()
            assert os.environ.get("HF_HUB_ENABLE_HF_TRANSFER") == "1"

    def test_prints_tip_when_package_missing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        env = {k: v for k, v in os.environ.items() if k != "HF_HUB_ENABLE_HF_TRANSFER"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch.dict("sys.modules", {"hf_transfer": None}),
        ):
            from vaudeville.setup import _enable_hf_transfer

            _enable_hf_transfer()
        assert "hf-transfer" in capsys.readouterr().out

    def test_skips_when_env_already_set(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch.dict(os.environ, {"HF_HUB_ENABLE_HF_TRANSFER": "1"}):
            from vaudeville.setup import _enable_hf_transfer

            _enable_hf_transfer()
        assert capsys.readouterr().out == ""


class TestHandleHfDownloadError:
    def _make_mock_errors(self) -> tuple[type, type, type]:
        class MockGatedRepoError(Exception):
            pass

        class MockRepoNotFoundError(Exception):
            pass

        class MockHfHubHTTPError(Exception):
            def __init__(self, status: int) -> None:
                self.response = MagicMock(status_code=status)

        return MockGatedRepoError, MockRepoNotFoundError, MockHfHubHTTPError

    def _mock_hf_modules(
        self,
        gated: type,
        not_found: type,
        http_err: type,
    ) -> dict[str, MagicMock]:
        mock_errors = MagicMock(
            GatedRepoError=gated,
            RepositoryNotFoundError=not_found,
            HfHubHTTPError=http_err,
        )
        mock_hub = MagicMock(errors=mock_errors)
        return {"huggingface_hub": mock_hub, "huggingface_hub.errors": mock_errors}

    def test_gated_repo_error_exits_1(self) -> None:
        gated, not_found, http_err = self._make_mock_errors()
        modules = self._mock_hf_modules(gated, not_found, http_err)
        with patch.dict("sys.modules", modules):
            from vaudeville.setup import _handle_hf_download_error

            with pytest.raises(SystemExit) as exc_info:
                _handle_hf_download_error(gated(), "my-repo")
        assert exc_info.value.code == 1

    def test_repo_not_found_error_exits_1(self) -> None:
        gated, not_found, http_err = self._make_mock_errors()
        modules = self._mock_hf_modules(gated, not_found, http_err)
        with patch.dict("sys.modules", modules):
            from vaudeville.setup import _handle_hf_download_error

            with pytest.raises(SystemExit) as exc_info:
                _handle_hf_download_error(not_found(), "my-repo")
        assert exc_info.value.code == 1

    def test_http_401_exits_1(self) -> None:
        gated, not_found, http_err = self._make_mock_errors()
        modules = self._mock_hf_modules(gated, not_found, http_err)
        with patch.dict("sys.modules", modules):
            from vaudeville.setup import _handle_hf_download_error

            with pytest.raises(SystemExit) as exc_info:
                _handle_hf_download_error(http_err(401), "my-repo")
        assert exc_info.value.code == 1

    def test_http_403_exits_1(self) -> None:
        gated, not_found, http_err = self._make_mock_errors()
        modules = self._mock_hf_modules(gated, not_found, http_err)
        with patch.dict("sys.modules", modules):
            from vaudeville.setup import _handle_hf_download_error

            with pytest.raises(SystemExit) as exc_info:
                _handle_hf_download_error(http_err(403), "my-repo")
        assert exc_info.value.code == 1

    def test_http_500_reraises(self) -> None:
        gated, not_found, http_err = self._make_mock_errors()
        modules = self._mock_hf_modules(gated, not_found, http_err)
        exc = http_err(500)
        with patch.dict("sys.modules", modules):
            from vaudeville.setup import _handle_hf_download_error

            with pytest.raises(type(exc)):
                _handle_hf_download_error(exc, "my-repo")

    def test_unrelated_exception_reraises(self) -> None:
        gated, not_found, http_err = self._make_mock_errors()
        modules = self._mock_hf_modules(gated, not_found, http_err)
        exc = ValueError("unexpected")
        with patch.dict("sys.modules", modules):
            from vaudeville.setup import _handle_hf_download_error

            with pytest.raises(ValueError, match="unexpected"):
                _handle_hf_download_error(exc, "my-repo")

    def test_import_error_fallback_reraises(self) -> None:
        """When huggingface_hub.errors is not importable, reraises original exception."""
        exc = RuntimeError("network failure")
        with patch.dict(
            "sys.modules", {"huggingface_hub": None, "huggingface_hub.errors": None}
        ):
            from vaudeville.setup import _handle_hf_download_error

            with pytest.raises(RuntimeError, match="network failure"):
                _handle_hf_download_error(exc, "my-repo")


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

    def test_hf_error_handled(self) -> None:
        class MockGatedRepoError(Exception):
            pass

        mock_errors = MagicMock(
            GatedRepoError=MockGatedRepoError,
            RepositoryNotFoundError=Exception,
            HfHubHTTPError=Exception,
        )
        mock_hub = MagicMock(errors=mock_errors)
        mock_load = MagicMock(side_effect=MockGatedRepoError())
        mock_mlx = MagicMock(load=mock_load)
        with patch.dict(
            "sys.modules",
            {
                "mlx_lm": mock_mlx,
                "huggingface_hub": mock_hub,
                "huggingface_hub.errors": mock_errors,
            },
        ):
            from vaudeville.setup import _setup_mlx

            with pytest.raises(SystemExit) as exc_info:
                _setup_mlx()
        assert exc_info.value.code == 1


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
            {
                "huggingface_hub": mock_hub,
                "llama_cpp": mock_llama_cpp,
                "llama_cpp.llama_cache": MagicMock(),
            },
        ):
            from vaudeville.setup import _setup_gguf

            _setup_gguf()
        mock_hub.hf_hub_download.assert_called_once()
        mock_llm.create_chat_completion.assert_called_once()

    def test_gated_repo_error_exits_1(self) -> None:
        class MockGatedRepoError(Exception):
            pass

        mock_errors = MagicMock(
            GatedRepoError=MockGatedRepoError,
            RepositoryNotFoundError=Exception,
            HfHubHTTPError=Exception,
        )
        mock_hub = MagicMock(
            hf_hub_download=MagicMock(side_effect=MockGatedRepoError()),
            errors=mock_errors,
        )
        with patch.dict(
            "sys.modules",
            {
                "huggingface_hub": mock_hub,
                "huggingface_hub.errors": mock_errors,
                "llama_cpp": MagicMock(),
            },
        ):
            from vaudeville.setup import _setup_gguf

            with pytest.raises(SystemExit) as exc_info:
                _setup_gguf()
        assert exc_info.value.code == 1


class TestEnsureRulesDir:
    def test_creates_rules_directory(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        with patch("vaudeville.setup.os.path.expanduser", return_value=str(fake_home)):
            from vaudeville.setup import _ensure_rules_dir

            _ensure_rules_dir()
        assert (fake_home / ".vaudeville" / "rules").is_dir()

    def test_idempotent_when_exists(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "home"
        rules_dir = fake_home / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        existing_file = rules_dir / "my-rule.yaml"
        existing_file.write_text("name: keep-me")
        with patch("vaudeville.setup.os.path.expanduser", return_value=str(fake_home)):
            from vaudeville.setup import _ensure_rules_dir

            _ensure_rules_dir()
        assert rules_dir.is_dir()
        assert existing_file.read_text() == "name: keep-me"


class TestSetupMain:
    def test_mlx_path_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("vaudeville.setup._detect_platform", return_value="mlx"),
            patch("vaudeville.setup._setup_mlx"),
            patch("vaudeville.setup._ensure_rules_dir"),
            patch("vaudeville.setup._check_disk_space"),
            patch("vaudeville.setup._enable_hf_transfer"),
        ):
            from vaudeville.setup import main

            main()
        assert "Setup complete" in capsys.readouterr().out

    def test_gguf_path_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("vaudeville.setup._detect_platform", return_value="gguf"),
            patch("vaudeville.setup._setup_gguf"),
            patch("vaudeville.setup._ensure_rules_dir"),
            patch("vaudeville.setup._check_disk_space"),
            patch("vaudeville.setup._enable_hf_transfer"),
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
            patch("vaudeville.setup._ensure_rules_dir"),
            patch("vaudeville.setup._check_disk_space"),
            patch("vaudeville.setup._enable_hf_transfer"),
        ):
            from vaudeville.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1

    def test_check_disk_space_called(self) -> None:
        mock_check = MagicMock()
        with (
            patch("vaudeville.setup._detect_platform", return_value="gguf"),
            patch("vaudeville.setup._setup_gguf"),
            patch("vaudeville.setup._ensure_rules_dir"),
            patch("vaudeville.setup._check_disk_space", mock_check),
            patch("vaudeville.setup._enable_hf_transfer"),
        ):
            from vaudeville.setup import main

            main()
        mock_check.assert_called_once()

    def test_enable_hf_transfer_called(self) -> None:
        mock_enable = MagicMock()
        with (
            patch("vaudeville.setup._detect_platform", return_value="gguf"),
            patch("vaudeville.setup._setup_gguf"),
            patch("vaudeville.setup._ensure_rules_dir"),
            patch("vaudeville.setup._check_disk_space"),
            patch("vaudeville.setup._enable_hf_transfer", mock_enable),
        ):
            from vaudeville.setup import main

            main()
        mock_enable.assert_called_once()


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

        response = {"verdict": "clean", "reason": "test ok", "confidence": 1.0}
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
            result = client.classify("test prompt")

        server_done.wait(timeout=3.0)
        assert result is not None
        assert result.verdict == "clean"
        assert result.reason == "test ok"


# --- Daemon extra coverage ---


class TestDaemonExtras:
    def test_cleanup_tolerates_missing_files(self) -> None:
        from vaudeville.server.daemon import DaemonConfig, VaudevilleDaemon

        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig(
                socket_path="/tmp/nonexistent-vd.sock",
                pid_file="/tmp/nonexistent-vd.pid",
                plugin_root=PROJECT_ROOT,
            ),
        )
        daemon._cleanup()  # should not raise

    def test_accept_loop_stops_on_stop_event(self) -> None:
        from vaudeville.server.daemon import DaemonConfig, VaudevilleDaemon

        with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
            sock_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".pid", dir="/tmp", delete=False) as f:
            pid_path = f.name
        os.unlink(sock_path)

        daemon = VaudevilleDaemon(
            MockBackend(), DaemonConfig(sock_path, pid_path, PROJECT_ROOT)
        )
        mock_server = MagicMock()
        mock_server.accept.side_effect = socket.timeout
        daemon._stop_event.set()
        daemon._accept_loop(mock_server)  # should return immediately

    def test_handle_client_exception_logged(self) -> None:
        from vaudeville.server.daemon import DaemonConfig, VaudevilleDaemon

        daemon = VaudevilleDaemon(
            MockBackend(),
            DaemonConfig("/tmp/x.sock", "/tmp/x.pid", PROJECT_ROOT),
        )
        broken_conn = MagicMock()
        broken_conn.recv.side_effect = OSError("connection reset")
        daemon._handle_client(broken_conn)  # should not raise
        broken_conn.close.assert_called_once()
