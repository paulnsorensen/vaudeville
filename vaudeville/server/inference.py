"""Abstract inference backend protocol.

Adding a new backend: create one file implementing InferenceBackend,
no changes to rules or hooks required.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..core.protocol import ClassifyResult


@runtime_checkable
class InferenceBackend(Protocol):
    def classify(self, prompt: str, max_tokens: int) -> str:
        """Run inference and return raw model output."""
        ...


@runtime_checkable
class LogprobBackend(InferenceBackend, Protocol):
    """Backend that also supports logprob extraction."""

    def classify_with_logprobs(self, prompt: str, max_tokens: int) -> ClassifyResult:
        """Run inference and return output with first-token logprobs."""
        ...


@runtime_checkable
class CachedBackend(InferenceBackend, Protocol):
    """Backend that supports KV cache prefix reuse."""

    def classify_cached(self, prompt: str, prefix_len: int, max_tokens: int) -> str:
        """Run inference reusing a precomputed KV cache for prompt[:prefix_len].

        Callers must pass max_tokens (typically CLASSIFY_MAX_TOKENS) to bound output.
        """
        ...


@runtime_checkable
class CachedLogprobBackend(CachedBackend, Protocol):
    """Backend that supports cached inference with logprob extraction."""

    def classify_cached_with_logprobs(
        self,
        prompt: str,
        prefix_len: int,
        max_tokens: int,
    ) -> ClassifyResult:
        """Cached inference with first-token logprobs.

        Callers must pass max_tokens (typically CLASSIFY_MAX_TOKENS) to bound output.
        """
        ...
