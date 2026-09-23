"""Cache for `load_layered`, keyed by project root and file mtimes (AC-24)."""

from __future__ import annotations

import functools
import os
from collections import OrderedDict

from .loader import _rule_filenames, layered_search_path, load_rules_layered
from .models import RuleSet

_Fingerprint = tuple[tuple[str, int], ...]

# Project roots kept at once; the least recently used root is evicted first.
_MAX_ROOTS = 32

# Only the newest entry per project root is kept; an older fingerprint for
# the same root is replaced on the next lookup instead of accumulating.
_cache: OrderedDict[str, tuple[_Fingerprint, RuleSet]] = OrderedDict()


@functools.lru_cache(maxsize=256)
def project_root_for(cwd: str) -> str:
    """Return the nearest directory at or above `cwd` that holds `.git`.

    Hook `cwd` follows `cd`, so a session in a subdirectory still loads the
    project's rules. Falls back to `cwd` when no `.git` is found.
    """
    current = os.path.abspath(cwd)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return cwd
        current = parent


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
    fingerprint per resolved project root is retained, for at most
    `_MAX_ROOTS` roots.
    """
    root_key = os.path.realpath(project_root) if project_root else ""
    fingerprint = _fingerprint(project_root)
    cached = _cache.get(root_key)
    if cached is not None and cached[0] == fingerprint:
        _cache.move_to_end(root_key)
        return cached[1]
    ruleset = load_rules_layered(project_root)
    _cache[root_key] = (fingerprint, ruleset)
    _cache.move_to_end(root_key)
    while len(_cache) > _MAX_ROOTS:
        _cache.popitem(last=False)
    return ruleset


def clear_cache() -> None:
    """Clear the `load_layered` cache. Test-only escape hatch."""
    _cache.clear()


def cache_size() -> int:
    """Number of project roots currently cached. Test-only introspection."""
    return len(_cache)
