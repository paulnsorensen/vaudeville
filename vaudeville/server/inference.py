"""Abstract inference backend protocol.

Adding a new backend: create one file implementing InferenceBackend,
no changes to rules or hooks required.
"""

from __future__ import annotations

from typing import Protocol

from ..core.protocol import ClassifyResult


class InferenceBackend(Protocol):
    def classify(self, prompt: str, max_tokens: int) -> str:
        """Run inference and return raw model output."""
        ...

    def classify_with_logprobs(self, prompt: str, max_tokens: int) -> ClassifyResult:
        """Run inference and return output with first-token logprobs."""
        ...
