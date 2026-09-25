"""Runtime paths for vaudeville daemon and client.

Per-UID directory in /tmp with 0700 permissions prevents other local
users from intercepting the Unix socket or tampering with state files.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

RUNTIME_DIR = f"/tmp/vaudeville-{os.getuid()}"
# VAUDEVILLE_SOCKET may be set by SessionStart via CLAUDE_ENV_FILE to avoid
# re-deriving the path in each hook. Treat empty string as unset.
SOCKET_PATH = os.environ.get("VAUDEVILLE_SOCKET") or str(
    pathlib.Path(RUNTIME_DIR) / "vaudeville.sock"
)
PID_FILE = str(pathlib.Path(RUNTIME_DIR) / "vaudeville.pid")
VERSION_FILE = str(pathlib.Path(RUNTIME_DIR) / "vaudeville.version")


def ensure_runtime_dir() -> None:
    """Create runtime directory with restrictive permissions if absent."""
    pathlib.Path(RUNTIME_DIR).mkdir(mode=0o700, exist_ok=True, parents=True)


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
