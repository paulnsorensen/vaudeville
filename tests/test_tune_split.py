"""Tests for vaudeville.tune.split — deterministic tune/held-out splitting."""

from __future__ import annotations

from vaudeville.eval import EvalCase
from vaudeville.tune.split import (
    SMALL_N_THRESHOLD,
    TUNE_RATIO,
    _compute_seed,
    split_cases,
)


def _make_cases(n: int) -> list[EvalCase]:
    return [EvalCase(text=f"case-{i}", label="violation") for i in range(n)]


class TestComputeSeed:
    def test_deterministic(self) -> None:
        s1 = _compute_seed("rule-a", 1234.0)
        s2 = _compute_seed("rule-a", 1234.0)
        assert s1 == s2

    def test_different_rule_different_seed(self) -> None:
        s1 = _compute_seed("rule-a", 1234.0)
        s2 = _compute_seed("rule-b", 1234.0)
        assert s1 != s2

    def test_different_mtime_different_seed(self) -> None:
        s1 = _compute_seed("rule-a", 1234.0)
        s2 = _compute_seed("rule-a", 5678.0)
        assert s1 != s2

    def test_returns_positive_int(self) -> None:
        seed = _compute_seed("test", 0.0)
        assert isinstance(seed, int)
        assert seed >= 0


class TestSplitCases:
    def test_small_n_returns_full_set(self) -> None:
        cases = _make_cases(SMALL_N_THRESHOLD - 1)
        tune, held = split_cases(cases, "rule")
        assert len(tune) == len(cases)
        assert len(held) == len(cases)

    def test_exact_threshold_splits(self) -> None:
        cases = _make_cases(SMALL_N_THRESHOLD)
        tune, held = split_cases(cases, "rule")
        assert len(tune) + len(held) == len(cases)
        assert len(tune) == int(SMALL_N_THRESHOLD * TUNE_RATIO)

    def test_70_30_split_ratio(self) -> None:
        cases = _make_cases(100)
        tune, held = split_cases(cases, "rule")
        assert len(tune) == 70
        assert len(held) == 30

    def test_deterministic_split(self) -> None:
        cases = _make_cases(20)
        t1, h1 = split_cases(cases, "rule-x", 42.0)
        t2, h2 = split_cases(cases, "rule-x", 42.0)
        assert [c.text for c in t1] == [c.text for c in t2]
        assert [c.text for c in h1] == [c.text for c in h2]

    def test_different_seed_different_split(self) -> None:
        cases = _make_cases(20)
        t1, _ = split_cases(cases, "rule-a", 1.0)
        t2, _ = split_cases(cases, "rule-b", 1.0)
        texts1 = [c.text for c in t1]
        texts2 = [c.text for c in t2]
        assert texts1 != texts2

    def test_no_overlap_in_split(self) -> None:
        cases = _make_cases(20)
        tune, held = split_cases(cases, "rule")
        tune_texts = {c.text for c in tune}
        held_texts = {c.text for c in held}
        assert tune_texts.isdisjoint(held_texts)

    def test_all_cases_covered(self) -> None:
        cases = _make_cases(20)
        tune, held = split_cases(cases, "rule")
        all_texts = {c.text for c in tune} | {c.text for c in held}
        expected = {c.text for c in cases}
        assert all_texts == expected

    def test_preserves_case_data(self) -> None:
        cases = [
            EvalCase(text="hello", label="violation"),
            EvalCase(text="world", label="clean"),
        ] * 6  # 12 cases, above threshold
        tune, held = split_cases(cases, "rule")
        for c in tune + held:
            assert c.label in ("violation", "clean")

    def test_empty_cases(self) -> None:
        tune, held = split_cases([], "rule")
        assert tune == []
        assert held == []

    def test_mtime_default_zero(self) -> None:
        cases = _make_cases(15)
        tune, held = split_cases(cases, "rule")
        assert len(tune) + len(held) == 15
