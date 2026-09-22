"""Entry point for the vaudeville daemon.

Usage: uv run python -m vaudeville.server [--socket PATH] [--pid-file PATH]

Defaults to per-UID runtime directory (singleton daemon).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ..core.paths import PID_FILE, SOCKET_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Vaudeville hook daemon")
    parser.add_argument("--socket", default=SOCKET_PATH, help="Unix socket path")
    parser.add_argument("--pid-file", default=PID_FILE, help="PID file path")
    args = parser.parse_args()

    log_level = (
        logging.DEBUG if os.environ.get("VAUDEVILLE_DEBUG") == "1" else logging.INFO
    )
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [vaudeville] %(message)s",
        stream=sys.stderr,
    )

    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        str(Path(__file__).parent.parent.parent),
    )

    from .daemon import acquire_pid_lock

    pid_fd = acquire_pid_lock(args.pid_file)
    if pid_fd is None:
        logging.info("Another instance holds PID lock — exiting")
        return

    from .daemon import DaemonConfig, VaudevilleDaemon
    from .event_log import EventLogger

    try:
        event_logger: EventLogger | None = EventLogger()
    except Exception as exc:
        logging.warning(
            "Failed to initialize event logger; continuing without it: %s", exc
        )
        event_logger = None

    config = DaemonConfig(
        socket_path=args.socket,
        pid_file=args.pid_file,
        plugin_root=plugin_root,
    )
    daemon = VaudevilleDaemon(
        config=config,
        pid_fd=pid_fd,
        event_logger=event_logger,
    )
    daemon.serve()


if __name__ == "__main__":
    main()