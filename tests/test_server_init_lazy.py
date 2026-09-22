"""`vaudeville.server`'s daemon import is lazy: only `stats`/`watch` (used by
the `stats`/`watch` CLI subcommands) load eagerly, so a subprocess that only
needs those never pulls in pydantic-ai.
"""

from __future__ import annotations

import subprocess
import sys


class TestServerStatsImportStaysLight:
    def test_import_server_stats_leaves_pydantic_ai_unloaded(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import vaudeville.server.stats, sys; "
                "print('pydantic_ai' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", result.stderr


class TestServerDaemonImportStillLazyLoads:
    def test_daemon_attribute_access_imports_daemon_module(self) -> None:
        import vaudeville.server as server

        daemon_cls = server.VaudevilleDaemon
        assert daemon_cls.__name__ == "VaudevilleDaemon"

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        import vaudeville.server as server

        try:
            server.does_not_exist
        except AttributeError:
            pass
        else:
            raise AssertionError("expected AttributeError for unknown attribute")


class TestServerDirListsPublicNames:
    def test_dir_includes_lazy_attributes(self) -> None:
        import vaudeville.server as server

        names = dir(server)
        assert names == sorted(server.__all__)
        assert "VaudevilleDaemon" in names
        assert "DaemonConfig" in names
