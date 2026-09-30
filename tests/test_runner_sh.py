"""Tests for hooks/runner.sh — the uv wrapper every command hook runs through.

A fake ``uv`` on PATH stands in for the real one, so no venv is built.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER_SH = os.path.join(PROJECT_ROOT, "hooks", "runner.sh")
BASH = shutil.which("bash") or "/bin/bash"


def _fake_uv(fake_bin: pathlib.Path, body: str) -> None:
    fake_bin.mkdir(exist_ok=True)
    uv = fake_bin / "uv"
    uv.write_text(f"#!{BASH}\n{body}\n")
    uv.chmod(0o755)


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, RUNNER_SH, "--harness", "claude-code"],
        env=env,
        input="{}",
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.fixture()
def env(tmp_path: pathlib.Path) -> dict[str, str]:
    base = os.environ.copy()
    base["PATH"] = f"{tmp_path / 'bin'}:{base['PATH']}"
    base["CLAUDE_PLUGIN_ROOT"] = PROJECT_ROOT
    base.pop("CLAUDE_PLUGIN_DATA", None)
    base.pop("UV_PROJECT_ENVIRONMENT", None)
    return base


def test_passes_args_and_plugin_data_venv_to_uv(
    env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    seen = tmp_path / "seen.txt"
    _fake_uv(
        tmp_path / "bin",
        f'echo "$UV_PROJECT_ENVIRONMENT" > {seen}\necho "$*" >> {seen}\necho \'{{"ok":1}}\'',
    )
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path / "data")

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '{"ok":1}'
    venv, args = seen.read_text().splitlines()
    assert venv == str(tmp_path / "data" / "venv")
    assert args == (
        f"run --project {PROJECT_ROOT} python {PROJECT_ROOT}/hooks/runner.py --harness claude-code"
    )


def test_leaves_uv_default_venv_without_plugin_data(
    env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    seen = tmp_path / "seen.txt"
    _fake_uv(tmp_path / "bin", f'echo "${{UV_PROJECT_ENVIRONMENT:-}}" > {seen}')

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert seen.read_text().strip() == ""


def test_fails_open_when_uv_errors(env: dict[str, str], tmp_path: pathlib.Path) -> None:
    """uv's own exit 2 would read as a Claude Code block; the wrapper allows."""
    _fake_uv(tmp_path / "bin", "echo 'uv: cannot create venv' >&2\nexit 2")

    result = _run(env)

    assert result.returncode == 0


def test_fails_open_when_uv_missing(env: dict[str, str], tmp_path: pathlib.Path) -> None:
    empty_bin = tmp_path / "empty"
    empty_bin.mkdir()
    env["PATH"] = str(empty_bin)

    result = _run(env)

    assert result.returncode == 0
    assert result.stdout == ""
