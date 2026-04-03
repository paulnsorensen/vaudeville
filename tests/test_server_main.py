from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from conftest import MockBackend


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
    def test_mlx_path_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("vaudeville.setup._detect_platform", return_value="mlx"),
            patch("vaudeville.setup._setup_mlx"),
        ):
            from vaudeville.setup import main

            main()
        assert "Setup complete" in capsys.readouterr().out

    def test_gguf_path_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
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


class TestInitBackend:
    def test_mlx_path(self) -> None:
        mock_mlx_cls = MagicMock(return_value=MagicMock())
        with (
            patch("vaudeville.server.__main__.MLXBackend", mock_mlx_cls, create=True),
            patch.dict(
                "sys.modules",
                {
                    "vaudeville.server.mlx_backend": MagicMock(
                        MLXBackend=mock_mlx_cls, DEFAULT_MODEL="m"
                    )
                },
            ),
        ):
            from vaudeville.server.__main__ import _init_backend

            _init_backend("mlx", "my-model")
        mock_mlx_cls.assert_called_once()

    def test_gguf_path(self) -> None:
        mock_gguf_cls = MagicMock(return_value=MagicMock())
        with patch.dict(
            "sys.modules",
            {"vaudeville.server.gguf_backend": MagicMock(GGUFBackend=mock_gguf_cls)},
        ):
            from vaudeville.server.__main__ import _init_backend

            _init_backend("gguf", None)
        mock_gguf_cls.assert_called_once()

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
                    "--backend",
                    "mlx",
                ],
            ),
            patch(
                "vaudeville.server.__main__._init_backend", return_value=mock_backend
            ),
            patch(
                "vaudeville.server.daemon.acquire_pid_lock",
                return_value=mock_pid_fd,
            ),
            patch("vaudeville.server.daemon.VaudevilleDaemon", mock_daemon_cls),
        ):
            from vaudeville.server.__main__ import main

            main()
        mock_daemon.serve.assert_called_once()

    def test_main_auto_detects_backend(self) -> None:
        mock_backend = MockBackend()
        mock_daemon = MagicMock()
        mock_pid_fd = MagicMock()

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
                "vaudeville.server.daemon.acquire_pid_lock",
                return_value=mock_pid_fd,
            ),
            patch(
                "vaudeville.server.daemon.VaudevilleDaemon",
                MagicMock(return_value=mock_daemon),
            ),
        ):
            from vaudeville.server.__main__ import main

            main()
        mock_daemon.serve.assert_called_once()
