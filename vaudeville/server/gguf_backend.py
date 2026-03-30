"""llama-cpp-python inference backend for CPU (Linux/macOS).

Loads Phi-3-mini Q4 GGUF via llama-cpp-python. No GPU required.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.protocol import ClassifyResult

DEFAULT_REPO = "microsoft/Phi-3-mini-4k-instruct-gguf"
DEFAULT_FILE = "Phi-3-mini-4k-instruct-q4.gguf"
TOP_LOGPROBS = 10

logger = logging.getLogger(__name__)


class GGUFBackend:
    """InferenceBackend implementation using llama-cpp-python on CPU."""

    def __init__(
        self,
        repo_id: str = DEFAULT_REPO,
        filename: str = DEFAULT_FILE,
    ) -> None:
        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama

        model_path = hf_hub_download(repo_id=repo_id, filename=filename)
        self._llm = Llama(
            model_path=model_path,
            n_ctx=4096,
            n_gpu_layers=0,
            verbose=False,
        )

    def classify(self, prompt: str, max_tokens: int = 50) -> str:
        """Run inference on prompt, return raw text output."""
        response = self._llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        result: str = response["choices"][0]["message"]["content"]
        return result

    def classify_with_logprobs(
        self, prompt: str, max_tokens: int = 50
    ) -> ClassifyResult:
        """Run inference and return output with first-token logprobs."""
        response: Any = self._llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.0,
            logprobs=True,
            top_logprobs=TOP_LOGPROBS,
        )
        text: str = response["choices"][0]["message"]["content"]
        logprobs = self._extract_first_token_logprobs(response)
        return ClassifyResult(text=text, logprobs=logprobs)

    def _extract_first_token_logprobs(self, response: Any) -> dict[str, float]:
        """Extract first-token logprobs from OpenAI-format response."""
        try:
            content = response["choices"][0]["logprobs"]["content"]
            if not content:
                return {}
            top = content[0]["top_logprobs"]
            return {entry["token"]: entry["logprob"] for entry in top}
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("[vaudeville] Logprob extraction failed: %s", exc)
            return {}
