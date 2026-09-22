from typing import TYPE_CHECKING, Any

from .event_log import ClassificationEvent, EventLogger
from .log_config import LogConfig, load_log_config
from .stats import aggregate_events, empty_result
from .watch import watch

__all__ = [
    "ClassificationEvent",
    "DaemonConfig",
    "EventLogger",
    "LogConfig",
    "VaudevilleDaemon",
    "aggregate_events",
    "empty_result",
    "load_log_config",
    "watch",
]

# `daemon` pulls in the hook pipeline, which pulls in pydantic-ai; keep it
# out of `import vaudeville.server` so CLI subcommands that only need
# `stats`/`watch` never load a heavy, network-capable dependency. The
# TYPE_CHECKING import keeps static types precise without a runtime cost.
if TYPE_CHECKING:
    from .daemon import DaemonConfig, VaudevilleDaemon


def __getattr__(name: str) -> Any:
    if name in ("DaemonConfig", "VaudevilleDaemon"):
        from . import daemon

        return getattr(daemon, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
