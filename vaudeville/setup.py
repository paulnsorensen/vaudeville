"""Model download and verification for vaudeville.

Usage: uv run --group mlx python -m vaudeville.setup   # Apple Silicon
       uv run --group gguf python -m vaudeville.setup   # CPU (Linux/x86)
"""

from __future__ import annotations

import platform
import sys


def _detect_platform() -> str:
    """Detect which backend this platform should use."""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mlx"
    return "gguf"


def _setup_mlx() -> None:
    from mlx_lm import generate, load

    model_id = "mlx-community/Phi-4-mini-instruct-4bit"
    print(f"Downloading {model_id} (~2.4 GB, Apple Silicon int4)...")
    model_obj, tokenizer, *_ = load(model_id)
    print("Model loaded. Verifying inference...")
    _ = generate(
        model_obj,
        tokenizer,
        prompt="VERDICT: clean",
        max_tokens=5,
        verbose=False,
    )
    print("Inference verified.")


def _setup_gguf() -> None:
    from huggingface_hub import hf_hub_download

    repo_id = "microsoft/Phi-4-mini-instruct-gguf"
    filename = "Phi-4-mini-instruct-q4.gguf"
    print(f"Downloading {repo_id}/{filename} (~2.3 GB, CPU Q4)...")
    model_path = hf_hub_download(repo_id=repo_id, filename=filename)
    print(f"Model cached at {model_path}. Verifying inference...")

    from llama_cpp import Llama

    llm = Llama(model_path=model_path, n_ctx=512, n_gpu_layers=0, verbose=False)
    response = llm.create_chat_completion(
        messages=[{"role": "user", "content": "VERDICT: clean"}],
        max_tokens=5,
        temperature=0.0,
    )
    _ = response["choices"][0]["message"]["content"]
    print("Inference verified.")


def main() -> None:
    backend = _detect_platform()
    print(f"Detected platform: {platform.system()}/{platform.machine()} → {backend}")
    print("This is a one-time setup step.\n")

    try:
        if backend == "mlx":
            _setup_mlx()
        else:
            _setup_gguf()
    except ImportError as exc:
        group = "mlx" if backend == "mlx" else "gguf"
        print(
            f"ERROR: Missing dependency ({exc}). Run: uv sync --group {group}",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\nSetup complete. Vaudeville is ready.")


if __name__ == "__main__":
    main()
