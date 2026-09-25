"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# Ensure vaudeville package is importable from project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class FakeRalphRunner:
    """Mock ralph runner that returns scripted responses and records calls."""

    def __init__(self) -> None:
        self.responses: list[
            tuple[Callable[[], None] | None, subprocess.CompletedProcess[str]]
        ] = []
        self.calls: list[tuple[str, list[str], str]] = []

    def add_response(
        self,
        side_effect_fn: Callable[[], None] | None,
        completed_process: subprocess.CompletedProcess[str],
    ) -> None:
        """Queue a response: optional side effect + CompletedProcess to return."""
        self.responses.append((side_effect_fn, completed_process))

    def __call__(
        self, ralph_dir: str, extra_args: list[str], project_root: str
    ) -> subprocess.CompletedProcess[str]:
        """Called by orchestrator; pops first queued response."""
        self.calls.append((ralph_dir, extra_args, project_root))

        if not self.responses:
            return subprocess.CompletedProcess(
                args=["ralph", "run", ralph_dir],
                returncode=1,
                stdout="",
                stderr="No more mock responses queued",
            )

        side_effect_fn, completed_process = self.responses.pop(0)
        if side_effect_fn:
            side_effect_fn()

        return completed_process


@pytest.fixture
def rules_dir() -> str:
    return os.path.join(PROJECT_ROOT, "rules")


@pytest.fixture
def short_sock_path() -> Iterator[str]:
    """A short /tmp-rooted directory for AF_UNIX sockets.

    macOS enforces a 104-byte AF_UNIX path limit; pytest `tmp_path` nests
    deep enough to exceed it. This fixture keeps socket paths short.
    """
    directory = tempfile.mkdtemp(prefix="vd-", dir="/tmp")
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolate_rule_layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test from loading the real bundled or user rule layers."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "no-plugin-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "no-home"))
