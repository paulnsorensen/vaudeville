"""llama-cpp-python inference backend for CPU (Linux/macOS).

Loads Phi-4-mini Q4 GGUF via llama-cpp-python. No GPU required.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.protocol import ClassifyResult

CACHE_CAPACITY = 2 * 1024 * 1024 * 1024  # 2 GB

DEFAULT_REPO = "microsoft/Phi-4-mini-instruct-gguf"
DEFAULT_FILE = "Phi-4-mini-instruct-q4.gguf"
TOP_LOGPROBS = 10
SYSTEM_PROMPT = (
    "You are a binary classifier. Respond with exactly `VERDICT: violation` "
    "or `VERDICT: clean` followed by `REASON: <one sentence>`. No other text."
)

GBNF_GRAMMAR = r"""root ::= "VERDICT: " verdict "\nREASON: " reason
verdict ::= "violation" | "clean"
reason ::= [^\n]{1,200}"""

_compiled_grammar: Any = None


def _get_grammar() -> Any:
    """Return the compiled GBNF grammar, caching after first call."""
    global _compiled_grammar  # noqa: PLW0603
    if _compiled_grammar is None:
        from llama_cpp import LlamaGrammar

        _compiled_grammar = LlamaGrammar.from_string(GBNF_GRAMMAR)
    return _compiled_grammar


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
        from llama_cpp.llama_cache import LlamaRAMCache

        model_path = hf_hub_download(repo_id=repo_id, filename=filename)
        self._llm = Llama(
            model_path=model_path,
            n_ctx=4096,
            n_gpu_layers=0,
            verbose=False,
        )
        self._llm.set_cache(LlamaRAMCache(capacity_bytes=CACHE_CAPACITY))

    def classify(self, prompt: str, max_tokens: int = 50) -> str:
        """Run inference on prompt, return raw text output."""
        response = self._llm.create_chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
            repeat_penalty=1.1,
            grammar=_get_grammar(),
        )
        result: str = response["choices"][0]["message"]["content"]
        return result

    def classify_with_logprobs(
        self, prompt: str, max_tokens: int = 50
    ) -> ClassifyResult:
        """Run inference and return output with first-token logprobs.

        Pre-fills "VERDICT:" via assistant message so the first generated
        token IS the class label, matching MLX backend behavior.
        """
        response: Any = self._llm.create_chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "VERDICT:"},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
            repeat_penalty=1.1,
            logprobs=True,
            top_logprobs=TOP_LOGPROBS,
        )
        text: str = "VERDICT:" + response["choices"][0]["message"]["content"]
        logprobs = self._extract_first_token_logprobs(response)
        return ClassifyResult(text=text, logprobs=logprobs)

    def _extract_first_token_logprobs(self, response: Any) -> dict[str, float]:
        """Extract logprobs from the first token position.

        With the "VERDICT:" assistant prefill, the first generated token
        should be the class label ("violation"/"clean").
        """
        try:
            content = response["choices"][0]["logprobs"]["content"]
            if not content:
                return {}
            return {
                entry["token"]: entry["logprob"] for entry in content[0]["top_logprobs"]
            }
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("[vaudeville] Logprob extraction failed: %s", exc)
            return {}
