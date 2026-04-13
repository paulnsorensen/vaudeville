"""MLX-LM inference backend for Apple Silicon.

Loads Phi-4-mini int4 via mlx_lm. Model is cached by Hugging Face hub.
"""

from __future__ import annotations

import copy
import hashlib
import logging
from typing import Any, cast

from ..core.protocol import ClassifyResult

DEFAULT_MODEL = "mlx-community/Phi-4-mini-instruct-4bit"
TOP_K_LOGPROBS = 10
SYSTEM_PROMPT = (
    "You are a binary classifier. Respond with exactly `VERDICT: violation` "
    "or `VERDICT: clean` followed by `REASON: <one sentence>`. No other text."
)

logger = logging.getLogger(__name__)


class MLXBackend:
    def __init__(self, model_path: str = DEFAULT_MODEL) -> None:
        from mlx_lm import load, stream_generate
        from mlx_lm.generate import generate_step

        self._model, self._tokenizer, *_ = load(model_path)
        self._stream_generate = stream_generate
        self._generate_step = generate_step
        self._prefix_caches: dict[str, list[Any]] = {}

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
            token_id: int = int(token.item() if hasattr(token, "item") else token)
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

    def classify_cached(
        self, prompt: str, prefix_len: int, max_tokens: int = 50,
    ) -> str:
        """Run inference using KV cache for the static prefix."""
        formatted_prefix = self._format_prefix(prompt[:prefix_len])
        prefix_key = hashlib.md5(formatted_prefix.encode()).hexdigest()

        base_cache = self._prefix_caches.get(prefix_key)
        if base_cache is None:
            base_cache = self._warm_prefix(formatted_prefix)
            self._prefix_caches[prefix_key] = base_cache

        request_cache = copy.deepcopy(base_cache)
        formatted_suffix = self._format_suffix(prompt[prefix_len:])
        suffix_tokens = self._tokenizer.encode(
            formatted_suffix, add_special_tokens=False,
        )

        parts: list[str] = []
        for response in self._stream_generate(
            self._model, self._tokenizer,
            prompt=suffix_tokens,
            max_tokens=max_tokens,
            prompt_cache=request_cache,
        ):
            parts.append(response.text)
            if response.finish_reason is not None:
                break
        return "".join(parts)

    def classify_cached_with_logprobs(
        self, prompt: str, prefix_len: int, max_tokens: int = 50,
    ) -> ClassifyResult:
        """Cached inference with logprob extraction."""
        import mlx.core as mx

        formatted_prefix = self._format_prefix(prompt[:prefix_len])
        prefix_key = hashlib.md5(formatted_prefix.encode()).hexdigest()

        base_cache = self._prefix_caches.get(prefix_key)
        if base_cache is None:
            base_cache = self._warm_prefix(formatted_prefix)
            self._prefix_caches[prefix_key] = base_cache

        request_cache = copy.deepcopy(base_cache)
        formatted_suffix = self._format_suffix(prompt[prefix_len:]) + "VERDICT:"
        suffix_tokens = mx.array(
            self._tokenizer.encode(formatted_suffix, add_special_tokens=False)
        )

        tokens: list[int] = []
        first_logprobs: dict[str, float] = {}

        for i, (token, logprobs_arr) in enumerate(
            self._generate_step(
                suffix_tokens, self._model,
                max_tokens=max_tokens, prompt_cache=request_cache,
            )
        ):
            token_id = int(token.item() if hasattr(token, "item") else token)
            tokens.append(token_id)
            if i == 0:
                first_logprobs = self._extract_top_logprobs(
                    logprobs_arr, self._tokenizer,
                )
            if i + 1 >= max_tokens:
                break
            eos = getattr(self._tokenizer, "eos_token_id", None)
            if eos is not None and token_id == eos:
                break

        text = "VERDICT:" + self._tokenizer.decode(tokens)
        return ClassifyResult(text=text, logprobs=first_logprobs)

    def _warm_prefix(self, formatted_prefix: str) -> list[Any]:
        """Precompute KV cache for a formatted prefix string."""
        import mlx.core as mx
        from mlx_lm.models.cache import make_prompt_cache

        tokens = mx.array(self._tokenizer.encode(formatted_prefix))
        cache = make_prompt_cache(self._model)
        for _ in self._generate_step(
            tokens, self._model, max_tokens=0, prompt_cache=cache,
        ):
            pass
        mx.eval([c.state for c in cache])
        return cache

    def _format_prefix(self, user_prefix: str) -> str:
        """Format the prefix through the chat template opening."""
        tokenizer: Any = self._tokenizer
        if hasattr(tokenizer, "apply_chat_template"):
            full: str = tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "SPLIT_MARKER"},
                ],
                tokenize=False,
                add_generation_prompt=True,
            )
            opening, _, _ = full.partition("SPLIT_MARKER")
            return opening + user_prefix
        return (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{user_prefix}"
        )

    def _format_suffix(self, user_suffix: str) -> str:
        """Format the suffix with closing chat template tags."""
        tokenizer: Any = self._tokenizer
        if hasattr(tokenizer, "apply_chat_template"):
            full: str = tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "SPLIT_MARKER"},
                ],
                tokenize=False,
                add_generation_prompt=True,
            )
            _, _, closing = full.partition("SPLIT_MARKER")
            return user_suffix + closing
        return f"{user_suffix}<|im_end|>\n<|im_start|>assistant\n"

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
            for idx in cast(list[Any], top_indices.tolist()):
                token_str: str = tokenizer.decode([idx])
                result[token_str] = logprobs_arr[idx].item()
            return result
        except Exception as exc:
            logger.warning("[vaudeville] Logprob extraction failed: %s", exc)
            return {}

    def _apply_chat_template(self, prompt: str) -> str:
        """Format prompt using the model's chat template."""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        tokenizer: Any = self._tokenizer
        if hasattr(tokenizer, "apply_chat_template"):
            formatted: str = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
            return formatted
        # Fallback ChatML format if no chat_template method
        return (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
        )
