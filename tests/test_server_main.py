from __future__ import annotations

import os
from pathlib import Path
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

    def test_disk_space_checked_before_download(self) -> None:
        call_order: list[str] = []
        with (
            patch("vaudeville.setup._detect_platform", return_value="gguf"),
            patch(
                "vaudeville.setup._check_disk_space",
                side_effect=lambda: call_order.append("disk"),
            ),
            patch(
                "vaudeville.setup._enable_hf_transfer",
                side_effect=lambda: call_order.append("transfer"),
            ),
            patch(
                "vaudeville.setup._setup_gguf",
                side_effect=lambda: call_order.append("setup"),
            ),
            patch("vaudeville.setup._ensure_rules_dir"),
        ):
            from vaudeville.setup import main

            main()
        assert call_order.index("disk") < call_order.index("setup")
        assert call_order.index("transfer") < call_order.index("setup")

    def test_size_announcement_printed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch("vaudeville.setup._detect_platform", return_value="gguf"),
            patch("vaudeville.setup._setup_gguf"),
            patch("vaudeville.setup._ensure_rules_dir"),
            patch("vaudeville.setup._check_disk_space"),
            patch("vaudeville.setup._enable_hf_transfer"),
        ):
            from vaudeville.setup import main

            main()
        out = capsys.readouterr().out
        assert "~/.cache/huggingface" in out
        assert "2.4 GB" in out


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
        mock_usage = MagicMock(free=500_000_000)
        with (
            patch("vaudeville.setup.os.path.expanduser", return_value=cache_dir),
            patch("vaudeville.setup.shutil.disk_usage", return_value=mock_usage),
        ):
            from vaudeville.setup import _check_disk_space

            with pytest.raises(SystemExit) as exc_info:
                _check_disk_space()
        assert exc_info.value.code == 1


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
