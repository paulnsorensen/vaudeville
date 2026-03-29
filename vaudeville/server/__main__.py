"""Entry point for the vaudeville daemon.

Usage: uv run python -m vaudeville.server [--socket PATH] [--pid-file PATH]

Defaults to /tmp/vaudeville.sock and /tmp/vaudeville.pid (singleton daemon).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .inference import InferenceBackend


def main() -> None:
    parser = argparse.ArgumentParser(description="Vaudeville inference daemon")
    parser.add_argument(
        "--socket", default="/tmp/vaudeville.sock", help="Unix socket path"
    )
    parser.add_argument(
        "--pid-file", default="/tmp/vaudeville.pid", help="PID file path"
    )
    parser.add_argument(
        "--backend", default="mlx", choices=["mlx", "gguf"], help="Inference backend"
    )
    parser.add_argument(
        "--model",
        default="mlx-community/Phi-3-mini-4k-instruct-4bit",
        help="Model path or Hugging Face ID",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [vaudeville] %(message)s",
        stream=sys.stderr,
    )

    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        str(Path(__file__).parent.parent.parent),
    )
    logging.info("Loading backend: %s model=%s", args.backend, args.model)
    backend: InferenceBackend
    if args.backend == "mlx":
        from .mlx_backend import MLXBackend

        backend = MLXBackend(args.model)
    else:
        from .gguf_backend import GGUFBackend

        backend = GGUFBackend()

    logging.info("Backend ready")

    from .daemon import VaudevilleDaemon

    daemon = VaudevilleDaemon(
        socket_path=args.socket,
        pid_file=args.pid_file,
        plugin_root=plugin_root,
        backend=backend,
    )
    daemon.serve()


if __name__ == "__main__":
    main()
