"""Tests for vaudeville.tune.pool — candidate authoring and pool I/O."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from vaudeville.core.rules import Example, Rule
from vaudeville.eval import CaseResult
from vaudeville.tune.pool import (
    AUTHOR_INTERVAL,
    MAX_NEW_CANDIDATES,
    STALL_THRESHOLD,
    _build_author_prompt,
    _next_candidate_id,
    _parse_authored,
    author_candidates,
    collect_fn_texts,
    compute_stall_count,
    inject_candidates,
    load_candidates,
    should_author,
    write_candidates,
)


class TestShouldAuthor:
    def test_stall_triggers(self) -> None:
        assert should_author(1, STALL_THRESHOLD) is True

    def test_stall_over_threshold(self) -> None:
        assert should_author(1, STALL_THRESHOLD + 2) is True

    def test_interval_triggers(self) -> None:
        assert should_author(AUTHOR_INTERVAL, 0) is True
        assert should_author(AUTHOR_INTERVAL * 2, 0) is True

    def test_no_trigger_trial_zero(self) -> None:
        assert should_author(0, 0) is False

    def test_no_trigger_mid_interval(self) -> None:
        assert should_author(2, 1) is False

    def test_stall_at_trial_zero(self) -> None:
        assert should_author(0, STALL_THRESHOLD) is True


class TestCollectFnTexts:
    def test_extracts_false_negatives(self) -> None:
        results = [
            CaseResult("r", 0, "missed", "violation", "clean", 0.6),
            CaseResult("r", 1, "caught", "violation", "violation", 0.9),
            CaseResult("r", 2, "ok", "clean", "clean", 0.8),
            CaseResult("r", 3, "also missed", "violation", "clean", 0.5),
        ]
        texts = collect_fn_texts(results)
        assert texts == ["missed", "also missed"]

    def test_empty_results(self) -> None:
        assert collect_fn_texts([]) == []

    def test_no_false_negatives(self) -> None:
        results = [
            CaseResult("r", 0, "caught", "violation", "violation", 0.9),
            CaseResult("r", 1, "ok", "clean", "clean", 0.8),
        ]
        assert collect_fn_texts(results) == []

    def test_ignores_false_positives(self) -> None:
        results = [
            CaseResult("r", 0, "wrong", "clean", "violation", 0.7),
        ]
        assert collect_fn_texts(results) == []


class TestComputeStallCount:
    def test_improving_no_stall(self) -> None:
        vals = [(0.5, 0.9), (0.6, 0.9), (0.7, 0.9)]
        assert compute_stall_count(vals) == 0

    def test_all_stalled(self) -> None:
        vals = [(0.8, 0.9), (0.7, 0.9), (0.6, 0.9)]
        assert compute_stall_count(vals) == 2

    def test_single_trial(self) -> None:
        assert compute_stall_count([(0.5, 0.9)]) == 0

    def test_empty(self) -> None:
        assert compute_stall_count([]) == 0

    def test_improvement_then_stall(self) -> None:
        vals = [(0.5, 0.9), (0.8, 0.9), (0.7, 0.9), (0.6, 0.9)]
        assert compute_stall_count(vals) == 2

    def test_stall_then_improvement(self) -> None:
        vals = [(0.8, 0.9), (0.7, 0.9), (0.9, 0.9)]
        assert compute_stall_count(vals) == 0

    def test_equal_values_count_as_stall(self) -> None:
        vals = [(0.8, 0.9), (0.8, 0.9), (0.8, 0.9)]
        assert compute_stall_count(vals) == 2


class TestNextCandidateId:
    def test_empty_list(self) -> None:
        assert _next_candidate_id([]) == 1

    def test_with_existing(self) -> None:
        assert _next_candidate_id(["authored-1", "authored-3"]) == 4

    def test_ignores_non_authored(self) -> None:
        assert _next_candidate_id(["ex1", "c1", "authored-2"]) == 3

    def test_handles_invalid_suffix(self) -> None:
        assert _next_candidate_id(["authored-abc", "authored-1"]) == 2


class TestBuildAuthorPrompt:
    def test_contains_rule_name(self) -> None:
        prompt = _build_author_prompt("my-rule", ["text1"])
        assert "my-rule" in prompt

    def test_contains_fn_texts(self) -> None:
        prompt = _build_author_prompt("r", ["fn1", "fn2"])
        assert "fn1" in prompt
        assert "fn2" in prompt

    def test_caps_fn_texts_at_10(self) -> None:
        texts = [f"text-{i}" for i in range(15)]
        prompt = _build_author_prompt("r", texts)
        assert "text-9" in prompt
        assert "text-10" not in prompt

    def test_contains_json_format(self) -> None:
        prompt = _build_author_prompt("r", ["t"])
        assert '"candidates"' in prompt


class TestParseAuthored:
    def test_parses_valid_json(self) -> None:
        raw = json.dumps(
            {
                "candidates": [
                    {"input": "bad thing", "reason": "it's bad"},
                    {"input": "worse thing", "reason": "it's worse"},
                ]
            }
        )
        examples = _parse_authored(raw, 1)
        assert len(examples) == 2
        assert examples[0].id == "authored-1"
        assert examples[0].input == "bad thing"
        assert examples[0].label == "violation"
        assert examples[1].id == "authored-2"

    def test_truncates_to_max(self) -> None:
        raw = json.dumps(
            {
                "candidates": [
                    {"input": f"item-{i}", "reason": f"r-{i}"} for i in range(10)
                ]
            }
        )
        examples = _parse_authored(raw, 1)
        assert len(examples) == MAX_NEW_CANDIDATES

    def test_start_id_offset(self) -> None:
        raw = json.dumps({"candidates": [{"input": "x", "reason": "y"}]})
        examples = _parse_authored(raw, 5)
        assert examples[0].id == "authored-5"

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            _parse_authored("not json", 1)

    def test_missing_key_raises(self) -> None:
        with pytest.raises(KeyError):
            _parse_authored('{"wrong": []}', 1)


class TestAuthorCandidates:
    def _mock_client(self, response_text: str) -> MagicMock:
        client = MagicMock()
        content_block = MagicMock()
        content_block.text = response_text
        client.messages.create.return_value = MagicMock(content=[content_block])
        return client

    def test_generates_candidates(self) -> None:
        payload = json.dumps(
            {
                "candidates": [
                    {"input": "bad output", "reason": "missed violation"},
                ]
            }
        )
        client = self._mock_client(payload)
        result = author_candidates(client, "test-rule", ["fn1"], [])
        assert len(result) == 1
        assert result[0].id == "authored-1"
        assert result[0].label == "violation"

    def test_empty_fn_texts_returns_empty(self) -> None:
        client = MagicMock()
        assert author_candidates(client, "r", [], []) == []
        client.messages.create.assert_not_called()

    def test_llm_error_returns_empty(self) -> None:
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("API down")
        result = author_candidates(client, "r", ["fn"], [])
        assert result == []

    def test_respects_existing_ids(self) -> None:
        payload = json.dumps({"candidates": [{"input": "x", "reason": "y"}]})
        client = self._mock_client(payload)
        result = author_candidates(client, "r", ["fn"], ["authored-3"])
        assert result[0].id == "authored-4"


class TestWriteAndLoadCandidates:
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "candidates.yaml")
            candidates = [
                Example("authored-1", "bad", "violation", "reason1"),
                Example("authored-2", "worse", "violation", "reason2"),
            ]
            write_candidates(path, candidates)
            loaded = load_candidates(path)
            assert len(loaded) == 2
            assert loaded[0].id == "authored-1"
            assert loaded[0].input == "bad"
            assert loaded[1].id == "authored-2"

    def test_append_to_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "candidates.yaml")
            batch1 = [Example("authored-1", "a", "violation", "r1")]
            batch2 = [Example("authored-2", "b", "violation", "r2")]
            write_candidates(path, batch1)
            write_candidates(path, batch2)
            loaded = load_candidates(path)
            assert len(loaded) == 2

    def test_load_nonexistent_returns_empty(self) -> None:
        assert load_candidates("/nonexistent/path.yaml") == []

    def test_load_invalid_yaml_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "bad.yaml")
            Path(path).write_text("just a string")
            assert load_candidates(path) == []

    def test_write_creates_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "candidates.yaml")
            candidates = [Example("a-1", "x", "violation", "r")]
            write_candidates(path, candidates)
            with open(path) as f:
                data = yaml.safe_load(f)
            assert "authored_at" in data["candidates"][0]

    def test_write_skips_corrupt_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "bad.yaml")
            Path(path).write_text("just a string")
            candidates = [Example("a-1", "x", "violation", "r")]
            write_candidates(path, candidates)
            # File should be unchanged — not overwritten
            assert Path(path).read_text() == "just a string"

    def test_load_skips_incomplete_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "candidates.yaml")
            data = {
                "candidates": [
                    {"id": "a-1", "input": "x", "label": "violation", "reason": "r"},
                    {"id": "a-2", "input": "y"},
                ]
            }
            with open(path, "w") as f:
                yaml.dump(data, f)
            loaded = load_candidates(path)
            assert len(loaded) == 1
            assert loaded[0].id == "a-1"


class TestInjectCandidates:
    def test_extends_candidates(self) -> None:
        rule = Rule(
            name="test",
            event="Stop",
            prompt="test",
            context=[],
            action="block",
            message="",
            candidates=[Example("c1", "old", "violation", "r")],
        )
        new = [Example("authored-1", "new", "violation", "r")]
        updated = inject_candidates(rule, new)
        assert len(updated.candidates) == 2
        assert updated.candidates[1].id == "authored-1"

    def test_preserves_rule_fields(self) -> None:
        rule = Rule(
            name="my-rule",
            event="Stop",
            prompt="p",
            context=[{"field": "x"}],
            action="block",
            message="m",
            threshold=0.7,
            examples=[Example("ex1", "e", "violation", "r")],
        )
        updated = inject_candidates(rule, [])
        assert updated.name == "my-rule"
        assert updated.threshold == 0.7
        assert updated.examples == rule.examples
        assert updated.context == rule.context

    def test_does_not_mutate_original(self) -> None:
        rule = Rule(
            name="test",
            event="Stop",
            prompt="p",
            context=[],
            action="block",
            message="",
        )
        new = [Example("a-1", "x", "violation", "r")]
        updated = inject_candidates(rule, new)
        assert len(rule.candidates) == 0
        assert len(updated.candidates) == 1
