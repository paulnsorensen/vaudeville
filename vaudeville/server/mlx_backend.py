"""MLX-LM inference backend for Apple Silicon.

Loads Phi-3-mini int4 via mlx_lm. Model is cached by Hugging Face hub.
"""

from __future__ import annotations

from typing import Any

DEFAULT_MODEL = "mlx-community/Phi-3-mini-4k-instruct-4bit"


class MLXBackend:
    def __init__(self, model_path: str = DEFAULT_MODEL) -> None:
        from mlx_lm import load, generate

        self._model, self._tokenizer = load(model_path)  # type: ignore[misc]
        self._generate = generate

    def classify(self, prompt: str, max_tokens: int = 50) -> str:
        """Run inference on prompt, return raw text output."""
        formatted = self._apply_chat_template(prompt)
        result: str = self._generate(
            self._model,
            self._tokenizer,
            prompt=formatted,
            max_tokens=max_tokens,
            temp=0.0,
            verbose=False,
        )
        return result

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
