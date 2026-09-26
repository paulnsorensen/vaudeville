"""Tests that the Pi package manifest parses and its extension paths exist.

PR C ships one root `package.json` with `pi.extensions` (Pi) and
`omp.extensions` (oh-my-pi marketplace installs); this only checks
the manifest shape and path existence, not TypeScript load/type validity
(covered separately by `node --experimental-strip-types --check`, run
manually since Node is not a project build dependency).
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_package_json() -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((REPO_ROOT / "package.json").read_text())
    return manifest


class TestPiPackageManifest:
    def test_manifest_parses_and_has_required_fields(self) -> None:
        manifest = _load_package_json()
        assert manifest["name"] == "vaudeville"
        assert "pi-package" in manifest["keywords"]

    def test_extension_paths_exist_relative_to_the_package_root(self) -> None:
        manifest = _load_package_json()
        extensions = manifest["pi"]["extensions"]
        assert extensions, "pi.extensions must list at least one extension"
        for relative_path in extensions:
            assert (REPO_ROOT / relative_path).is_file(), relative_path

    def test_omp_extensions_match_pi_extensions(self) -> None:
        """oh-my-pi marketplace installs read `omp.extensions`, not `pi.extensions`."""
        manifest = _load_package_json()
        assert manifest["omp"]["extensions"] == manifest["pi"]["extensions"]

    def test_no_omp_hook_dirs_that_would_double_enforce(self) -> None:
        """oh-my-pi's Claude-plugin loader runs `hooks/pre|post/*`; keep them absent."""
        assert not (REPO_ROOT / "hooks" / "pre").exists()
        assert not (REPO_ROOT / "hooks" / "post").exists()
