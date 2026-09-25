"""Tests confirming vaudeville.core no longer exports the verdict protocol or Rule."""

from __future__ import annotations

import os
import pathlib
import re

import pytest

import vaudeville.core

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DELETED_SYMBOLS = re.compile(r"\b(sanitize_input|parse_verdict|compute_confidence)\b")
_SCAN_TARGETS = ["vaudeville", "hooks"]


def test_rule_import_raises() -> None:
    with pytest.raises(ImportError):
        from vaudeville.core import Rule  # type: ignore[attr-defined]  # noqa: F401


def test_parse_verdict_not_exported() -> None:
    assert not hasattr(vaudeville.core, "parse_verdict")


def test_compute_confidence_not_exported() -> None:
    assert not hasattr(vaudeville.core, "compute_confidence")


def test_no_deleted_symbol_anywhere_in_package() -> None:
    """AC-25: no sanitize_input, parse_verdict, or compute_confidence symbol
    remains anywhere under vaudeville/ or hooks/."""
    matches: list[str] = []
    for target in _SCAN_TARGETS:
        target_path = os.path.join(PROJECT_ROOT, target)
        for root, _dirs, names in os.walk(target_path):
            for name in names:
                if not name.endswith(".py"):
                    continue
                file_path = os.path.join(root, name)
                with pathlib.Path(file_path).open(encoding="utf-8") as f:
                    text = f.read()
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if _DELETED_SYMBOLS.search(line):
                        matches.append(f"{file_path}:{lineno}: {line.strip()}")
    assert matches == []
