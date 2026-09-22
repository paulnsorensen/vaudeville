"""Cache for `load_layered`, keyed by project root and file mtimes (AC-24)."""

from __future__ import annotations

import os

from .loader import _rule_filenames, layered_search_path, load_rules_layered
from .models import RuleSet

_Fingerprint = tuple[tuple[str, int], ...]

# Only the newest entry per project root is kept; an older fingerprint for
# the same root is evicted on the next lookup instead of accumulating.
_cache: dict[str, tuple[_Fingerprint, RuleSet]] = {}


def _fingerprint(project_root: str | None) -> _Fingerprint:
    stats: list[tuple[str, int]] = []
    for rules_dir in layered_search_path(project_root):
        for name in _rule_filenames(rules_dir):
            path = os.path.join(rules_dir, name)
            try:
                stats.append((path, os.stat(path).st_mtime_ns))
            except OSError:
                continue
    return tuple(stats)


def load_layered(project_root: str | None = None) -> RuleSet:
    """Load rules for `project_root`, cached by directory contents and mtimes.

    An edited, added, or removed rule file -- or a different project root
    -- misses the cache and reloads from disk (AC-24). Only the newest
    fingerprint per project root is retained.
    """
    root_key = project_root or ""
    fingerprint = _fingerprint(project_root)
    cached = _cache.get(root_key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    ruleset = load_rules_layered(project_root)
    _cache[root_key] = (fingerprint, ruleset)
    return ruleset


def clear_cache() -> None:
    """Clear the `load_layered` cache. Test-only escape hatch."""
    _cache.clear()


def cache_size() -> int:
    """Number of project roots currently cached. Test-only introspection."""
    return len(_cache)
