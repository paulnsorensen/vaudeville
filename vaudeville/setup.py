"""Model download and verification for vaudeville.

Usage: uv run python -m vaudeville.setup
"""
from __future__ import annotations

import os
import sys

DEFAULT_MODEL = "mlx-community/Phi-3-mini-4k-instruct-4bit"
EXPECTED_SIZE_GB = 2.4


def main() -> None:
    model = os.environ.get("VAUDEVILLE_MODEL", DEFAULT_MODEL)
    print(f"Downloading {model} (~{EXPECTED_SIZE_GB} GB, Apple Silicon int4)...")
    print("This is a one-time setup step.")

    try:
        from mlx_lm import load
    except ImportError:
        print("ERROR: mlx-lm not installed. Run: uv sync", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching model from Hugging Face hub...")
    model_obj, tokenizer = load(model)  # type: ignore[misc]
    print(f"Model loaded successfully.")

    # Verify inference with a short prompt
    from mlx_lm import generate
    test_prompt = "VERDICT: clean\nREASON: test"
    _ = generate(model_obj, tokenizer, prompt=test_prompt, max_tokens=5, verbose=False)
    print("Inference verified.")
    print(f"\nSetup complete. Vaudeville is ready.")


if __name__ == "__main__":
    main()
