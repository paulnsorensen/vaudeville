"""Abstract inference backend protocol.

Adding a new backend: create one file implementing InferenceBackend,
no changes to rules or hooks required.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class InferenceBackend(Protocol):
    def classify(self, prompt: str, max_tokens: int) -> str:
        """Run inference and return raw model output."""
        ...
