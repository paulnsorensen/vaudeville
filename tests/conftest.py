"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import sys

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


@pytest.fixture
def rules_dir() -> str:
    return os.path.join(PROJECT_ROOT, "rules")


@pytest.fixture
def tests_dir() -> str:
    return os.path.join(PROJECT_ROOT, "examples", "tests")
