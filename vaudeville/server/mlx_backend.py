"""MLX-LM inference backend for Apple Silicon.

Loads Phi-4-mini int4 via mlx_lm. Model is cached by Hugging Face hub.
"""

from __future__ import annotations

import collections
import copy
import hashlib
import logging
from typing import Any, cast

from ..core.protocol import ClassifyResult

DEFAULT_MODEL = "mlx-community/Phi-4-mini-instruct-4bit"
TOP_K_LOGPROBS = 10
MAX_PREFIX_CACHES = 16
# The system prompt constrains output to `VERDICT: x\nREASON: <sentence>`.
# We count newlines in generated output and halt after two (one following
# the VERDICT line, one following the REASON line). mlx_lm has no native
# stop-sequence or GBNF equivalent to the llama.cpp grammar.
REASON_NEWLINE_STOP = 2
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
        self._prefix_caches: collections.OrderedDict[str, list[Any]] = (
            collections.OrderedDict()
        )
        self._chat_template_parts: tuple[str, str] | None = None
        # Cache newline token IDs once so _collect_tokens can use a set
        # membership check instead of per-token decode.
        self._newline_token_ids: frozenset[int] = frozenset(
            self._tokenizer.encode("\n", add_special_tokens=False)
        )

    def classify(self, prompt: str, max_tokens: int = 50) -> str:
        """Run inference on prompt, return raw text output."""
        formatted = self._apply_chat_template(prompt)
        parts: list[str] = []
        newlines = 0
        for response in self._stream_generate(
            self._model,
            self._tokenizer,
            prompt=formatted,
            max_tokens=max_tokens,
        ):
            parts.append(response.text)
            newlines += response.text.count("\n")
            if response.finish_reason is not None or newlines >= REASON_NEWLINE_STOP:
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

        formatted = self._apply_chat_template(prompt) + "VERDICT:"
        prompt_tokens = mx.array(self._tokenizer.encode(formatted))
        tokens, first_logprobs = self._collect_tokens(
            prompt_tokens,
            max_tokens,
        )
        text = "VERDICT:" + self._tokenizer.decode(tokens)
        return ClassifyResult(text=text, logprobs=first_logprobs)

    def classify_cached(
        self,
        prompt: str,
        prefix_len: int,
        max_tokens: int = 50,
    ) -> str:
        """Run inference using KV cache for the static prefix."""
        request_cache = self._get_or_warm_cache(prompt[:prefix_len])
        formatted_suffix = self._format_suffix(prompt[prefix_len:])
        suffix_tokens = self._tokenizer.encode(
            formatted_suffix,
            add_special_tokens=False,
        )
        parts: list[str] = []
        newlines = 0
        for response in self._stream_generate(
            self._model,
            self._tokenizer,
            prompt=suffix_tokens,
            max_tokens=max_tokens,
            prompt_cache=request_cache,
        ):
            parts.append(response.text)
            newlines += response.text.count("\n")
            if response.finish_reason is not None or newlines >= REASON_NEWLINE_STOP:
                break
        return "VERDICT:" + "".join(parts)

    def _get_or_warm_cache(self, user_prefix: str) -> list[Any]:
        """Return a deepcopy of the KV cache for a prefix, warming on first call.

        On first warm, validates that split tokenization matches full tokenization.
        Logs a warning if BPE boundary effects cause divergence.
        """
        formatted_prefix = self._format_prefix(user_prefix)
        prefix_key = hashlib.md5(formatted_prefix.encode()).hexdigest()
        base_cache = self._prefix_caches.get(prefix_key)
        if base_cache is None:
            self._validate_token_boundary(formatted_prefix, user_prefix)
            base_cache = self._warm_prefix(formatted_prefix)
            self._prefix_caches[prefix_key] = base_cache
            while len(self._prefix_caches) > MAX_PREFIX_CACHES:
                self._prefix_caches.popitem(last=False)
        else:
            self._prefix_caches.move_to_end(prefix_key)
        return copy.deepcopy(base_cache)

    def _validate_token_boundary(
        self,
        formatted_prefix: str,
        user_prefix: str,
    ) -> None:
        """Check that split tokenization matches full tokenization at the boundary."""
        sample_suffix = "sample input text"
        formatted_suffix = self._format_suffix(sample_suffix)
        full_formatted = self._apply_chat_template(user_prefix + sample_suffix)
        full_tokens = self._tokenizer.encode(full_formatted)
        split_tokens = self._tokenizer.encode(
            formatted_prefix
        ) + self._tokenizer.encode(formatted_suffix, add_special_tokens=False)
        if full_tokens != split_tokens:
            logger.warning(
                "BPE boundary mismatch: full=%d tokens, split=%d tokens "
                "(prefix may have suboptimal KV cache)",
                len(full_tokens),
                len(split_tokens),
            )

    def classify_cached_with_logprobs(
        self,
        prompt: str,
        prefix_len: int,
        max_tokens: int = 50,
    ) -> ClassifyResult:
        """Cached inference with logprob extraction."""
        import mlx.core as mx

        request_cache = self._get_or_warm_cache(prompt[:prefix_len])
        formatted_suffix = self._format_suffix(prompt[prefix_len:]) + "VERDICT:"
        suffix_tokens = mx.array(
            self._tokenizer.encode(formatted_suffix, add_special_tokens=False)
        )
        tokens, first_logprobs = self._collect_tokens(
            suffix_tokens,
            max_tokens,
            cache=request_cache,
        )
        text = "VERDICT:" + self._tokenizer.decode(tokens)
        return ClassifyResult(text=text, logprobs=first_logprobs)

    def _collect_tokens(
        self,
        prompt_tokens: Any,
        max_tokens: int,
        cache: list[Any] | None = None,
    ) -> tuple[list[int], dict[str, float]]:
        """Run generate_step collecting token IDs and first-token logprobs."""
        tokens: list[int] = []
        first_logprobs: dict[str, float] = {}
        newlines = 0
        kwargs: dict[str, Any] = {"max_tokens": max_tokens}
        if cache is not None:
            kwargs["prompt_cache"] = cache
        for i, (token, logprobs_arr) in enumerate(
            self._generate_step(prompt_tokens, self._model, **kwargs)
        ):
            token_id = int(token.item() if hasattr(token, "item") else token)
            tokens.append(token_id)
            if i == 0:
                first_logprobs = self._extract_top_logprobs(
                    logprobs_arr,
                    self._tokenizer,
                )
            if i + 1 >= max_tokens:
                break
            eos = getattr(self._tokenizer, "eos_token_id", None)
            if eos is not None and token_id == eos:
                break
            if token_id in self._newline_token_ids:
                newlines += 1
            if newlines >= REASON_NEWLINE_STOP:
                break
        return tokens, first_logprobs

    def _warm_prefix(self, formatted_prefix: str) -> list[Any]:
        """Precompute KV cache for a formatted prefix string."""
        import mlx.core as mx
        from mlx_lm.models.cache import make_prompt_cache

        tokens = mx.array(self._tokenizer.encode(formatted_prefix))
        cache: list[Any] = make_prompt_cache(self._model)
        for _ in self._generate_step(
            tokens,
            self._model,
            max_tokens=0,
            prompt_cache=cache,
        ):
            pass
        mx.eval([c.state for c in cache])
        return cache

    def _split_chat_template(self) -> tuple[str, str]:
        """Split the chat template into (opening, closing) around user content.

        Result is cached after the first call — the template is static.
        Returns the ChatML fallback if the tokenizer lacks apply_chat_template
        or if SPLIT_MARKER is not preserved in the output.
        """
        if self._chat_template_parts is not None:
            return self._chat_template_parts

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
            if "SPLIT_MARKER" in full:
                opening, _, closing = full.partition("SPLIT_MARKER")
                self._chat_template_parts = (opening, closing)
                return self._chat_template_parts
            logger.warning("SPLIT_MARKER not found in chat template — using fallback")
        opening = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n"
        closing = "<|im_end|>\n<|im_start|>assistant\n"
        self._chat_template_parts = (opening, closing)
        return self._chat_template_parts

    def _format_prefix(self, user_prefix: str) -> str:
        """Format the prefix through the chat template opening."""
        opening, _ = self._split_chat_template()
        return opening + user_prefix

    def _format_suffix(self, user_suffix: str) -> str:
        """Format the suffix with closing chat template tags."""
        _, closing = self._split_chat_template()
        return user_suffix + closing

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
