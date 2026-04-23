"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable

import pytest

from vaudeville.core.protocol import ClassifyResult

# Ensure vaudeville package is importable from project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class MockBackend:
    """Deterministic backend for tests — returns canned VERDICT/REASON output."""

    def __init__(
        self,
        verdict: str = "clean",
        reason: str = "test reason",
        logprobs: dict[str, float] | None = None,
    ) -> None:
        self.verdict = verdict
        self.reason = reason
        self.logprobs = logprobs or {}
        self.calls: list[str] = []

    def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
        self.calls.append(prompt)
        return f"VERDICT: {self.verdict}\nREASON: {self.reason}"

    def classify_with_logprobs(  # noqa: ARG002
        self, prompt: str, max_tokens: int = 50
    ) -> ClassifyResult:
        self.calls.append(prompt)
        return ClassifyResult(
            text=f"VERDICT: {self.verdict}\nREASON: {self.reason}",
            logprobs=self.logprobs,
        )


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
