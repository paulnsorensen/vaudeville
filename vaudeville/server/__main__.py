"""Entry point for the vaudeville daemon.

Usage: uv run python -m vaudeville.server [--socket PATH] [--pid-file PATH]

Defaults to per-UID runtime directory (singleton daemon).
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
import warnings
from pathlib import Path

from ..core.paths import PID_FILE, SOCKET_PATH
from .inference import InferenceBackend

# Suppress spurious "leaked semaphore" warnings from mlx_lm internals.
warnings.filterwarnings("ignore", message=".*leaked semaphore.*", category=UserWarning)


def detect_backend() -> str:
    """Auto-detect the best available inference backend for this platform."""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            import mlx_lm  # noqa: F401

            return "mlx"
        except ImportError:
            pass
    try:
        import llama_cpp  # noqa: F401

        return "gguf"
    except ImportError:
        pass
    raise RuntimeError(
        "No inference backend available. "
        "Install mlx-lm (Apple Silicon) or llama-cpp-python (CPU)."
    )


def _init_backend(backend_name: str, model: str | None) -> InferenceBackend:
    """Create the inference backend by name."""
    if backend_name == "mlx":
        from .mlx_backend import DEFAULT_MODEL, MLXBackend

        return MLXBackend(model or DEFAULT_MODEL)
    elif backend_name == "gguf":
        from .gguf_backend import GGUFBackend

        return GGUFBackend()
    logging.error("Unknown backend: %s", backend_name)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Vaudeville inference daemon")
    parser.add_argument("--socket", default=SOCKET_PATH, help="Unix socket path")
    parser.add_argument("--pid-file", default=PID_FILE, help="PID file path")
    parser.add_argument(
        "--backend",
        default="auto",
        choices=["mlx", "gguf", "auto"],
        help="Inference backend (default: auto-detect)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model path or Hugging Face ID (default: backend-specific)",
    )
    args = parser.parse_args()

    log_level = (
        logging.DEBUG if os.environ.get("VAUDEVILLE_DEBUG") == "1" else logging.INFO
    )
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [vaudeville] %(message)s",
        stream=sys.stderr,
    )

    backend_name = args.backend if args.backend != "auto" else detect_backend()

    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        str(Path(__file__).parent.parent.parent),
    )

    # Acquire PID lock BEFORE loading the model (2.4GB) to prevent
    # multiple processes from loading the model concurrently.
    from .daemon import acquire_pid_lock

    pid_fd = acquire_pid_lock(args.pid_file)
    if pid_fd is None:
        logging.info("Another instance holds PID lock — exiting")
        return

    logging.info("Loading backend: %s model=%s", backend_name, args.model or "default")
    backend = _init_backend(backend_name, args.model)
    logging.info("Backend ready")

    from .daemon import VaudevilleDaemon
    from .event_log import EventLogger

    try:
        event_logger: EventLogger | None = EventLogger()
    except Exception as exc:
        logging.warning(
            "Failed to initialize event logger; continuing without it: %s", exc
        )
        event_logger = None

    daemon = VaudevilleDaemon(
        socket_path=args.socket,
        pid_file=args.pid_file,
        plugin_root=plugin_root,
        backend=backend,
        pid_fd=pid_fd,
        event_logger=event_logger,
    )
    daemon.serve()


if __name__ == "__main__":
    main()
