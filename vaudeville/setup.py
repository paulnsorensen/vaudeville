"""Model download and verification for vaudeville.

Usage: uv run --group mlx python -m vaudeville.setup   # Apple Silicon
       uv run --group gguf python -m vaudeville.setup   # CPU (Linux/x86)
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import NoReturn

_MIN_FREE_BYTES: int = 3_500_000_000  # 3.5 GB


def _check_disk_space() -> None:
    """Fail fast if the HF cache partition has less than 3.5 GB free."""
    cache_dir = os.path.expanduser("~/.cache/huggingface")
    os.makedirs(cache_dir, exist_ok=True)
    free = shutil.disk_usage(cache_dir).free
    if free < _MIN_FREE_BYTES:
        print(
            f"ERROR: Insufficient disk space. "
            f"At least 3.5 GB is required in {cache_dir}, "
            f"but only {free / 1e9:.1f} GB is available. "
            "Free up space and re-run setup.",
            file=sys.stderr,
        )
        sys.exit(1)


def _enable_hf_transfer() -> None:
    """Enable hf_transfer for faster downloads if the package is installed."""
    if os.environ.get("HF_HUB_ENABLE_HF_TRANSFER"):
        return  # already configured by the caller
    try:
        import hf_transfer  # noqa: F401

        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
        print("hf_transfer detected — using accelerated download (5-10x faster).")
    except ImportError:
        print(
            "Tip: Install hf-transfer for 5-10x faster downloads: "
            "pip install hf-transfer"
        )


def _handle_hf_download_error(exc: BaseException, repo_id: str) -> NoReturn:
    """Translate known Hugging Face Hub errors into friendly messages, then exit or re-raise."""
    try:
        from huggingface_hub import errors as hf_errors

        if isinstance(exc, hf_errors.GatedRepoError):
            print(
                f"ERROR: '{repo_id}' is a gated repository — access requires "
                f"authentication.\n"
                f"  1. Run `huggingface-cli login`\n"
                f"  2. Request access at https://huggingface.co/{repo_id}",
                file=sys.stderr,
            )
            sys.exit(1)
        if isinstance(exc, hf_errors.RepositoryNotFoundError):
            print(
                f"ERROR: Repository '{repo_id}' was not found (404). "
                "Verify the repository URL and your network connection.",
                file=sys.stderr,
            )
            sys.exit(1)
        if isinstance(exc, hf_errors.HfHubHTTPError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                print(
                    f"ERROR: HTTP {status} when accessing '{repo_id}'. "
                    "Run `huggingface-cli login` to authenticate.",
                    file=sys.stderr,
                )
                sys.exit(1)
    except ImportError:
        pass
    raise exc


def _detect_platform() -> str:
    """Detect which backend this platform should use."""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mlx"
    return "gguf"


def _setup_mlx() -> None:
    from mlx_lm import generate, load

    model_id = "mlx-community/Phi-4-mini-instruct-4bit"
    print(f"Downloading {model_id} (~2.4 GB, Apple Silicon int4)...")
    try:
        model_obj, tokenizer, *_ = load(model_id)
    except Exception as exc:
        _handle_hf_download_error(exc, model_id)
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
    try:
        model_path = hf_hub_download(repo_id=repo_id, filename=filename)
    except Exception as exc:
        _handle_hf_download_error(exc, repo_id)
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


def _ensure_rules_dir() -> None:
    """Create ~/.vaudeville/rules/ if it doesn't exist."""
    rules_dir = os.path.join(os.path.expanduser("~"), ".vaudeville", "rules")
    os.makedirs(rules_dir, exist_ok=True)
    print(f"Rules directory ready: {rules_dir}")


def main() -> None:
    backend = _detect_platform()
    print(f"Detected platform: {platform.system()}/{platform.machine()} → {backend}")
    print("This will download ~2.4 GB to ~/.cache/huggingface (one-time setup).\n")

    _check_disk_space()
    _enable_hf_transfer()
    _ensure_rules_dir()

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
