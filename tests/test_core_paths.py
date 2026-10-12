"""Runtime-dir privacy checks: a socket dir another user can reach is refused."""

from __future__ import annotations

import os
import pathlib
import socket

import pytest

import vaudeville.core.paths as paths
from vaudeville.core.client import VaudevilleClient


def _private_dir(tmp_path: pathlib.Path, name: str = "private") -> pathlib.Path:
    directory = tmp_path / name
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    return directory


class TestIsPrivateDir:
    def test_owned_0700_dir_is_private(self, tmp_path: pathlib.Path) -> None:
        assert paths.is_private_dir(str(_private_dir(tmp_path)))

    @pytest.mark.parametrize("mode", [0o750, 0o705, 0o755, 0o777])
    def test_group_or_world_access_is_not_private(self, tmp_path: pathlib.Path, mode: int) -> None:
        directory = _private_dir(tmp_path)
        directory.chmod(mode)
        assert not paths.is_private_dir(str(directory))

    def test_symlink_to_private_dir_is_not_private(self, tmp_path: pathlib.Path) -> None:
        link = tmp_path / "link"
        link.symlink_to(_private_dir(tmp_path))
        assert not paths.is_private_dir(str(link))

    def test_file_is_not_private(self, tmp_path: pathlib.Path) -> None:
        regular = tmp_path / "file"
        regular.write_text("")
        regular.chmod(0o600)
        assert not paths.is_private_dir(str(regular))

    def test_missing_path_is_not_private(self, tmp_path: pathlib.Path) -> None:
        assert not paths.is_private_dir(str(tmp_path / "absent"))

    @pytest.mark.skipif(os.geteuid() == 0, reason="root owns the candidate dir")
    def test_dir_owned_by_another_uid_is_not_private(self) -> None:
        assert not paths.is_private_dir("/")


class TestEnsureRuntimeDir:
    def test_creates_missing_dir_0700(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime_dir = tmp_path / "runtime"
        monkeypatch.setattr(paths, "RUNTIME_DIR", str(runtime_dir))

        paths.ensure_runtime_dir()

        assert runtime_dir.stat().st_mode & 0o777 == 0o700

    def test_refuses_existing_dir_that_is_not_private(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime_dir = _private_dir(tmp_path)
        runtime_dir.chmod(0o777)
        monkeypatch.setattr(paths, "RUNTIME_DIR", str(runtime_dir))

        with pytest.raises(PermissionError, match="not a private directory"):
            paths.ensure_runtime_dir()


class TestClientRefusesSharedSocketDir:
    def test_socket_in_shared_dir_fails_open_without_connecting(
        self, short_sock_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another uid can plant a daemon in a shared dir; never talk to it."""
        sock_path = os.path.join(short_sock_path, "hostile.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)
        server.setblocking(False)
        os.chmod(short_sock_path, 0o777)
        monkeypatch.setattr("vaudeville.core.client.SOCKET_PATH", sock_path)
        try:
            response = VaudevilleClient().hook({"op": "hook"})
            with pytest.raises(BlockingIOError):
                server.accept()
        finally:
            server.close()

        assert response is None
