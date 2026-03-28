"""llama-cpp-python inference backend for CPU (Linux/macOS).

Loads Phi-3-mini Q4 GGUF via llama-cpp-python. No GPU required.
"""

from __future__ import annotations

DEFAULT_REPO = "microsoft/Phi-3-mini-4k-instruct-gguf"
DEFAULT_FILE = "Phi-3-mini-4k-instruct-q4.gguf"


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
