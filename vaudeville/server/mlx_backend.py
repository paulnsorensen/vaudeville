"""MLX-LM inference backend for Apple Silicon.

Loads Phi-3-mini int4 via mlx_lm. Model is cached by Hugging Face hub.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.protocol import ClassifyResult

DEFAULT_MODEL = "mlx-community/Phi-3-mini-4k-instruct-4bit"
TOP_K_LOGPROBS = 10

logger = logging.getLogger(__name__)


class MLXBackend:
    def __init__(self, model_path: str = DEFAULT_MODEL) -> None:
        from mlx_lm import load, stream_generate
        from mlx_lm.generate import generate_step

        self._model, self._tokenizer = load(model_path)
        self._stream_generate = stream_generate
        self._generate_step = generate_step

    def classify(self, prompt: str, max_tokens: int = 50) -> str:
        """Run inference on prompt, return raw text output."""
        formatted = self._apply_chat_template(prompt)
        parts: list[str] = []
        for response in self._stream_generate(
            self._model,
            self._tokenizer,
            prompt=formatted,
            max_tokens=max_tokens,
        ):
            parts.append(response.text)
            if response.finish_reason is not None:
                break
        return "".join(parts)

    def classify_with_logprobs(
        self, prompt: str, max_tokens: int = 50
    ) -> ClassifyResult:
        """Run inference and return output with first-token logprobs.

        Pre-fills "VERDICT:" so the first generated token IS the class label
        ("violation"/"clean"), giving meaningful logprob distributions.
        """
        import mlx.core as mx

        # Append anchor after chat template so model's first token = class label
        formatted = self._apply_chat_template(prompt) + "VERDICT:"
        tokenizer: Any = self._tokenizer
        prompt_tokens = mx.array(tokenizer.encode(formatted))

        tokens: list[int] = []
        first_logprobs: dict[str, float] = {}

        for i, (token, logprobs_arr) in enumerate(
            self._generate_step(prompt_tokens, self._model, max_tokens=max_tokens)
        ):
            token_id: int = token.item() if hasattr(token, "item") else int(token)
            tokens.append(token_id)

            if i == 0:
                first_logprobs = self._extract_top_logprobs(logprobs_arr, tokenizer)

            if i + 1 >= max_tokens:
                break
            eos = getattr(tokenizer, "eos_token_id", None)
            if eos is not None and token_id == eos:
                break

        # Prepend the anchor back so parse_verdict can parse the full output
        text: str = "VERDICT:" + tokenizer.decode(tokens)
        return ClassifyResult(text=text, logprobs=first_logprobs)

    def _extract_top_logprobs(
        self, logprobs_arr: Any, tokenizer: Any
    ) -> dict[str, float]:
        """Extract top-K token logprobs from the full vocab distribution."""
        import mlx.core as mx

        try:
            mx.eval(logprobs_arr)
            top_indices = mx.argpartition(logprobs_arr, kth=-TOP_K_LOGPROBS)[
                -TOP_K_LOGPROBS:
            ]
            mx.eval(top_indices)

            result: dict[str, float] = {}
            for idx in top_indices.tolist():
                token_str: str = tokenizer.decode([idx])
                result[token_str] = logprobs_arr[idx].item()
            return result
        except Exception as exc:
            logger.warning("[vaudeville] Logprob extraction failed: %s", exc)
            return {}

    def _apply_chat_template(self, prompt: str) -> str:
        """Format prompt using the model's chat template."""
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        tokenizer: Any = self._tokenizer
        if hasattr(tokenizer, "apply_chat_template"):
            formatted: str = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
            return formatted
        # Fallback for Phi-3 format if no chat_template method
        return f"<|user|>\n{prompt}<|end|>\n<|assistant|>\n"
