"""The Claude Code plugin and marketplace manifests parse and carry the
required fields (manifest-reference / marketplace-reference, read 2026-09-26).
"""

from __future__ import annotations

import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_JSON = os.path.join(PROJECT_ROOT, ".claude-plugin", "plugin.json")
MARKETPLACE_JSON = os.path.join(PROJECT_ROOT, ".claude-plugin", "marketplace.json")


def _load(path: str) -> dict[str, object]:
    with open(path, encoding="utf-8") as handle:
        return dict(json.load(handle))


class TestPluginManifest:
    def test_plugin_json_parses(self) -> None:
        manifest = _load(PLUGIN_JSON)
        assert isinstance(manifest, dict)

    def test_plugin_json_has_required_name(self) -> None:
        manifest = _load(PLUGIN_JSON)
        assert manifest["name"] == "vaudeville"


class TestMarketplaceManifest:
    def test_marketplace_json_parses(self) -> None:
        marketplace = _load(MARKETPLACE_JSON)
        assert isinstance(marketplace, dict)

    def test_marketplace_json_has_required_fields(self) -> None:
        marketplace = _load(MARKETPLACE_JSON)
        assert marketplace["name"]
        assert marketplace["owner"]
        assert marketplace["plugins"]

    def test_marketplace_json_has_no_dead_schema_field(self) -> None:
        """The old `$schema` URL 404s; the field is removed, not repointed."""
        marketplace = _load(MARKETPLACE_JSON)
        assert "$schema" not in marketplace

    def test_marketplace_plugin_entries_have_name(self) -> None:
        marketplace = _load(MARKETPLACE_JSON)
        plugins = marketplace["plugins"]
        assert isinstance(plugins, list)
        assert all(isinstance(entry, dict) and entry.get("name") for entry in plugins)
