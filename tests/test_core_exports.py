"""Tests confirming vaudeville.core no longer exports the verdict protocol or Rule."""

from __future__ import annotations

import pytest

import vaudeville.core


def test_rule_import_raises() -> None:
    with pytest.raises(ImportError):
        from vaudeville.core import Rule  # type: ignore[attr-defined]  # noqa: F401


def test_parse_verdict_not_exported() -> None:
    assert not hasattr(vaudeville.core, "parse_verdict")


def test_compute_confidence_not_exported() -> None:
    assert not hasattr(vaudeville.core, "compute_confidence")
