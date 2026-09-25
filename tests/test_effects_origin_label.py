"""Tests for the hook-origin label prefixed to feedback/rewrite text (AC-22)."""

from __future__ import annotations

from vaudeville.server.effects import with_origin_label


class TestOriginLabel:
    def test_labels_text_with_rule_name(self) -> None:
        labeled = with_origin_label("trim-secrets", "stop hardcoding keys")

        assert labeled.startswith("[vaudeville hook: trim-secrets]")
        assert labeled.endswith("stop hardcoding keys")

    def test_idempotent_does_not_double_prefix(self) -> None:
        once = with_origin_label("trim-secrets", "stop hardcoding keys")

        twice = with_origin_label("trim-secrets", once)

        assert once == twice
        assert twice.count("[vaudeville hook: trim-secrets]") == 1
