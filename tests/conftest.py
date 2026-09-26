"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Ensure vaudeville package is importable from project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


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
