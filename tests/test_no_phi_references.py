"""AC-19: no mlx, gguf, huggingface, or Phi reference remains, and the
Phi-era backend/setup files are gone."""

from __future__ import annotations

import os
import pathlib
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PATTERN = re.compile(r"mlx|gguf|huggingface|phi-4|\bphi\b", re.IGNORECASE)

_SCAN_TARGETS = [
    "vaudeville",
    "justfile",
    os.path.join(".github", "workflows", "ci.yml"),
    os.path.join("hooks", "session-start.sh"),
]

_DELETED_FILES = [
    os.path.join("vaudeville", "server", "condense.py"),
    os.path.join("vaudeville", "server", "inference.py"),
    os.path.join("vaudeville", "server", "mlx_backend.py"),
    os.path.join("vaudeville", "server", "mlx_logprobs.py"),
    os.path.join("vaudeville", "server", "gguf_backend.py"),
    os.path.join("vaudeville", "setup.py"),
    os.path.join("vaudeville", "core", "rules.py"),
    os.path.join("vaudeville", "core", "examples.py"),
]


def _iter_files(target: str) -> list[str]:
    path = os.path.join(PROJECT_ROOT, target)
    if pathlib.Path(path).is_file():
        return [path]
    if not pathlib.Path(path).is_dir():
        return []
    files = []
    for root, _dirs, names in os.walk(path):
        for name in names:
            files.append(os.path.join(root, name))
    return files


def test_no_phi_references() -> None:
    matches: list[str] = []
    for target in _SCAN_TARGETS:
        for file_path in _iter_files(target):
            try:
                with pathlib.Path(file_path).open(encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _PATTERN.search(line):
                    matches.append(f"{file_path}:{lineno}: {line.strip()}")
    assert matches == []


def test_deleted_files_do_not_exist() -> None:
    for rel_path in _DELETED_FILES:
        assert not pathlib.Path(os.path.join(PROJECT_ROOT, rel_path)).exists(), rel_path
