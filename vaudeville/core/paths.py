"""Runtime paths for vaudeville daemon and client.

Per-UID directory in /tmp with 0700 permissions prevents other local
users from intercepting the Unix socket or tampering with state files.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import tempfile


def _default_runtime_dir() -> str:
    """Per-UID dir on POSIX; the per-user temp dir on hosts without getuid."""
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return str(pathlib.Path(tempfile.gettempdir()) / "vaudeville")
    return f"/tmp/vaudeville-{getuid()}"


RUNTIME_DIR = _default_runtime_dir()
# VAUDEVILLE_SOCKET may be set by SessionStart via CLAUDE_ENV_FILE to avoid
# re-deriving the path in each hook. Treat empty string as unset.
SOCKET_PATH = os.environ.get("VAUDEVILLE_SOCKET") or str(
    pathlib.Path(RUNTIME_DIR) / "vaudeville.sock"
)
PID_FILE = str(pathlib.Path(RUNTIME_DIR) / "vaudeville.pid")
VERSION_FILE = str(pathlib.Path(RUNTIME_DIR) / "vaudeville.version")


def _is_trusted_ancestor(info: os.stat_result, uid: int) -> bool:
    """Only root or this user can change the entry, or the sticky bit guards it."""
    if info.st_uid not in (0, uid):
        return False
    if stat.S_ISLNK(info.st_mode):
        return True  # link mode bits are meaningless; its parent is checked too
    shared_write = info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    return not shared_write or bool(info.st_mode & stat.S_ISVTX)


def is_private_dir(path: str) -> bool:
    """True when path is a real directory that only the current user can use.

    A symlink, a dir that another uid owns, or a group/world-accessible dir
    can host a hostile socket. So can a dir below an ancestor that another
    user can rename or replace. Clients and the daemon refuse such a dir.
    """
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return False  # no uid model to verify ownership against
    uid = getuid()
    try:
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
            return False
        lexical = pathlib.Path(path).absolute().parents
        resolved = pathlib.Path(path).resolve().parents
        return all(_is_trusted_ancestor(os.lstat(a), uid) for a in {*lexical, *resolved})
    except OSError:
        return False


def ensure_runtime_dir() -> None:
    """Create the runtime directory 0700, or refuse one that is not private."""
    pathlib.Path(RUNTIME_DIR).mkdir(mode=0o700, exist_ok=True, parents=True)
    if not is_private_dir(RUNTIME_DIR):
        raise PermissionError(f"{RUNTIME_DIR} is not a private directory of this user")


def find_project_root() -> str | None:
    """Find the git working tree root, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None
