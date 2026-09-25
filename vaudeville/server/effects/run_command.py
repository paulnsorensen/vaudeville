"""Fire-and-forget named-command runner fed from `UserConfig.commands`."""

from __future__ import annotations

import fnmatch
import logging
import os
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
    `timeout`. Returns False and logs when `name` is not in `config.commands`
    or the process cannot start, so a `run` failure affects only that run.
    """
    argv = config.commands.get(name)
    if argv is None:
        logger.warning("run action names undefined command %r; skipped", name)
        return False
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_child_env(config),
        )
    except (OSError, ValueError) as exc:
        logger.warning("run command %r failed to start: %s; skipped", name, exc)
        return False

    def _enforce_timeout() -> None:
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.kill()

    threading.Thread(target=_enforce_timeout, daemon=True).start()

    def _write_stdin() -> None:
        try:
            if process.stdin is not None:
                process.stdin.write(event_json.encode("utf-8"))
                process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threading.Thread(target=_write_stdin, daemon=True).start()
    return True


# Case-sensitive uppercase patterns for env var names commonly used for
# credentials. This is a best-effort scrub, not an exhaustive guarantee:
# a secret under an unmatched name still reaches the child process.
_SECRET_NAME_PATTERNS: tuple[str, ...] = (
    "*_API_KEY",
    "*_AUTH_TOKEN",
    "*_TOKEN",
    "*SECRET*",
    "ANTHROPIC_*",
    "OPENAI_*",
    "AWS_*",
)


def _looks_like_secret_name(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in _SECRET_NAME_PATTERNS)


def _child_env(config: UserConfig) -> dict[str, str]:
    """Copy the parent env, dropping configured provider key env vars and
    names that look like credentials.

    Any project rule may invoke any configured command. This drops every
    configured `key_env` plus names matching common credential patterns
    (`*_API_KEY`, `*_AUTH_TOKEN`, `*_TOKEN`, `*SECRET*`, `ANTHROPIC_*`,
    `OPENAI_*`, `AWS_*`); it does not guarantee every secret is removed.
    """
    key_envs = {provider.key_env for provider in config.providers.values()}
    return {
        k: v for k, v in os.environ.items() if k not in key_envs and not _looks_like_secret_name(k)
    }
