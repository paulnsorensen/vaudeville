"""Cache for `load_layered`, keyed by project root and file mtimes (AC-24)."""

from __future__ import annotations

import os

from .loader import layered_search_path, load_rules_layered
from .models import RuleSet

_CacheKey = tuple[str, tuple[tuple[str, int], ...]]

_cache: dict[_CacheKey, RuleSet] = {}


def _fingerprint(project_root: str | None) -> _CacheKey:
    stats: list[tuple[str, int]] = []
    for rules_dir in layered_search_path(project_root):
        try:
            names = os.listdir(rules_dir)
        except OSError:
            continue
        for name in sorted(names):
            if not name.endswith((".yaml", ".yml")):
                continue
            path = os.path.join(rules_dir, name)
            try:
                stats.append((path, os.stat(path).st_mtime_ns))
            except OSError:
                continue
    return (project_root or "", tuple(stats))


def load_layered(project_root: str | None = None) -> RuleSet:
    """Load rules for `project_root`, cached by directory contents and mtimes.

    An edited, added, or removed rule file -- or a different project root
    -- misses the cache and reloads from disk (AC-24).
    """
    key = _fingerprint(project_root)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ruleset = load_rules_layered(project_root)
    _cache[key] = ruleset
    return ruleset


def clear_cache() -> None:
    """Clear the `load_layered` cache. Test-only escape hatch."""
    _cache.clear()
