"""Fire-and-forget named-command runner fed from `UserConfig.commands`."""

from __future__ import annotations

import logging
import subprocess
import threading

from vaudeville.server.user_config import UserConfig

logger = logging.getLogger(__name__)


def run_named_command(
    name: str,
    config: UserConfig,
    event_json: str,
    *,
    timeout: float,
) -> bool:
    """Start the named command without a shell, feeding `event_json` on stdin.

    Does not wait for the child; a daemon thread kills it if it outlives
    `timeout`. Returns False and logs when `name` is not in `config.commands`.
    """
    argv = config.commands.get(name)
    if argv is None:
        logger.warning("run action names undefined command %r; skipped", name)
        return False
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if process.stdin is not None:
        process.stdin.write(event_json.encode("utf-8"))
        process.stdin.close()

    def _enforce_timeout() -> None:
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.kill()

    threading.Thread(target=_enforce_timeout, daemon=True).start()
    return True
