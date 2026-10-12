from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, timeout=180)


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="vaudeville-wheel-") as directory:
        smoke = Path(directory)
        run(["uv", "build", "--wheel", "--out-dir", str(smoke / "dist")], project)
        (wheel,) = (smoke / "dist").glob("*.whl")
        requirements = smoke / "requirements.txt"
        run(
            [
                "uv",
                "export",
                "--quiet",
                "--locked",
                "--group",
                "dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                str(requirements),
            ],
            project,
        )
        environment = smoke / "venv"
        run(["uv", "venv", "--python", sys.executable, str(environment)], smoke)
        python = environment / "bin" / "python"
        run(["uv", "pip", "sync", "--python", str(python), str(requirements)], smoke)
        run(["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel)], smoke)
        shutil.copytree(
            project / "tests" / "bdd",
            smoke / "bdd",
            ignore=shutil.ignore_patterns(
                "__pycache__",
                ".pytest_cache",
            ),
        )
        plugin = smoke / "plugin"
        (plugin / "hooks").mkdir(parents=True)
        shutil.copy2(project / "hooks" / "runner.py", plugin / "hooks" / "runner.py")
        env = os.environ.copy()
        env["VAUDEVILLE_BDD_PLUGIN_ROOT"] = str(plugin)
        for name in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
            env.pop(name, None)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        subprocess.run(
            [str(python), "-I", "-m", "pytest", "-p", "pytest_bdd.plugin", "-q", "bdd"],
            cwd=smoke,
            env=env,
            check=True,
            timeout=180,
        )


if __name__ == "__main__":
    main()
