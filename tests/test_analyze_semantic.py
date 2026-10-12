"""Tests for the semantic Stop-rule analyzers and the final_messages table.

These tests ingest small JSONL transcripts into a real DuckDB file in a tmp
dir. The analyzers then query that file through the real duckdb CLI.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

import pytest

from vaudeville.analytics._ingest import build_database

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_HOOK_SCRIPTS = _ROOT / "skills" / "hook-suggester" / "scripts"
_INGEST_PATH = _ROOT / "skills" / "session-analytics" / "scripts" / "ingest.py"

if str(_HOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_HOOK_SCRIPTS))

if "analyzers" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("analyzers", _HOOK_SCRIPTS / "analyzers.py")
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["analyzers"] = _mod
    _spec.loader.exec_module(_mod)

analyzers = sys.modules["analyzers"]

pytestmark = pytest.mark.skipif(shutil.which("duckdb") is None, reason="duckdb CLI not installed")

SESSION = "sess-1"


def _ts(minute: int) -> str:
    return f"2099-01-01T00:{minute:02d}:00.000Z"


def _entry(minute: int, content: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": "assistant",
        "uuid": f"u-{minute}",
        "sessionId": SESSION,
        "cwd": "/proj",
        "gitBranch": "main",
        "timestamp": _ts(minute),
        "isSidechain": False,
        "message": {"content": content, "stop_reason": "end_turn"},
    }
    entry.update(extra)
    return entry


def _final(minute: int, text: str, **extra: Any) -> dict[str, Any]:
    return _entry(minute, [{"type": "text", "text": text}], **extra)


def _tool(minute: int, name: str, **tool_input: Any) -> dict[str, Any]:
    entry = _entry(
        minute, [{"type": "tool_use", "id": f"t-{minute}", "name": name, "input": tool_input}]
    )
    entry["message"]["stop_reason"] = "tool_use"
    return entry


def _build(tmp_path: pathlib.Path, records: list[dict[str, Any]]) -> pathlib.Path:
    projects = tmp_path / "projects" / "p"
    projects.mkdir(parents=True)
    (projects / "s.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    db = tmp_path / "sessions.duckdb"
    build_database(db, str(tmp_path / "projects" / "**" / "*.jsonl"))
    return db


def _rows(db: pathlib.Path, sql: str) -> list[dict[str, Any]]:
    out = subprocess.run(
        ["duckdb", str(db), "-json", "-c", sql], capture_output=True, text=True, check=True
    ).stdout.strip()
    return json.loads(out) if out else []


def _run(db: pathlib.Path, monkeypatch: pytest.MonkeyPatch, min_occ: int = 1) -> dict[str, Any]:
    monkeypatch.setattr(analyzers, "DB_PATH", str(db))
    # The fixture timestamps are in year 2099, so use a wide window.
    result = analyzers.check_semantic_candidates(40000, min_occ)
    return {s["id"]: s for s in result}


class TestFinalMessagesTable:
    def test_keeps_final_text_and_skips_sidechain_and_tool_turns(
        self, tmp_path: pathlib.Path
    ) -> None:
        db = _build(
            tmp_path,
            [
                _tool(1, "Bash", command="ls"),
                _final(2, "Done. Should I commit?"),
                _final(3, "Subagent chatter", isSidechain=True),
                _entry(4, [{"type": "thinking", "thinking": "hmm"}]),
                _final(5, "Second turn."),
            ],
        )
        rows = _rows(db, "SELECT text, turn_start FROM final_messages ORDER BY timestamp")
        assert [r["text"] for r in rows] == ["Done. Should I commit?", "Second turn."]
        assert rows[0]["turn_start"] == ""
        assert rows[1]["turn_start"] == _ts(2)

    def test_joins_multiple_text_blocks(self, tmp_path: pathlib.Path) -> None:
        db = _build(
            tmp_path,
            [_entry(1, [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])],
        )
        rows = _rows(db, "SELECT text FROM final_messages")
        assert rows == [{"text": "a\nb"}]


class TestSkillIngest:
    def test_skill_ingest_creates_final_messages(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        projects = tmp_path / "projects" / "p"
        projects.mkdir(parents=True)
        (projects / "s.jsonl").write_text(json.dumps(_final(1, "All done.")) + "\n")
        spec = importlib.util.spec_from_file_location("skill_ingest", _INGEST_PATH)
        assert spec is not None and spec.loader is not None
        ingest = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ingest)
        monkeypatch.setattr(ingest, "DB_DIR", str(tmp_path))
        monkeypatch.setattr(ingest, "DB_PATH", str(tmp_path / "sessions.duckdb"))
        monkeypatch.setattr(ingest, "DB_TMP_PATH", str(tmp_path / "sessions.duckdb.tmp"))
        monkeypatch.setattr(ingest, "JSONL_GLOB", str(tmp_path / "projects" / "**" / "*.jsonl"))
        monkeypatch.setattr(sys, "argv", ["ingest.py", "--force"])
        ingest.main()
        rows = _rows(tmp_path / "sessions.duckdb", "SELECT text FROM final_messages")
        assert rows == [{"text": "All done."}]


class TestSemanticCandidates:
    def test_git_permission_matches_tail_only(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        early = "Should I commit? " + "filler text. " * 80 + "Everything is finished."
        db = _build(
            tmp_path,
            [
                _final(1, "Tests pass and lint is clean. Want me to push and open a PR?"),
                _final(2, early),
            ],
        )
        found = _run(db, monkeypatch)["semantic-permission-to-git"]
        assert found["count"] == 1
        assert found["event"] == "Stop"
        assert found["hook_type"] == "slm-rule"
        assert found["tier"] == "shadow"
        assert found["target_tier"] == "block"
        assert found["covered_by"] == "git-gate"
        assert found["test_cases"] == [
            {"text": e, "outcome": "violation"} for e in found["examples"]
        ]
        assert "Want me to push and open a PR?" in found["examples"][0]

    def test_deferral_counts_and_covered_by(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _build(
            tmp_path,
            [
                _final(1, "Fixed A. I left B as a follow-up PR."),
                _final(2, "Fixed A. I will file a ticket for B."),
                _final(3, "Everything is fixed now in this change."),
            ],
        )
        found = _run(db, monkeypatch)["semantic-deferred-work"]
        assert found["count"] == 2
        assert found["covered_by"] == "deferral-detector"
        assert len(found["examples"]) == 2

    def test_unverified_completion_needs_edit_without_check(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _build(
            tmp_path,
            [
                # Turn 1: edit, no check, claim -> candidate.
                _tool(1, "Edit", file_path="a.py"),
                _final(2, "I changed the parser. This should work now."),
                # Turn 2: edit, test run, claim -> verified, not a candidate.
                _tool(3, "Edit", file_path="a.py"),
                _tool(4, "Bash", command="uv run pytest -q"),
                _final(5, "The parser is fixed. This fixes it."),
                # Turn 3: claim with no edits -> not a candidate.
                _final(6, "That should work as described."),
            ],
        )
        found = _run(db, monkeypatch)["semantic-unverified-completion"]
        assert found["count"] == 1
        assert "This should work now." in found["examples"][0]
        assert found["covered_by"] is None

    def test_hedged_claim_detected(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _build(
            tmp_path,
            [_final(1, "I believe the helper is already defined in utils.")],
        )
        found = _run(db, monkeypatch)["semantic-hedged-claims"]
        assert found["count"] == 1
        assert found["priority"] == "medium"

    def test_category_without_signal_is_skipped(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _build(
            tmp_path,
            [_final(1, "Should I commit?"), _final(2, "Should I push?")],
        )
        found = _run(db, monkeypatch, min_occ=3)
        assert found == {}

    def test_examples_are_capped_deduped_and_redacted(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "sk-abcdefghijklmnop1234"
        records = [
            _final(i, f"Done with token={secret} part {i}. Should I commit?") for i in range(1, 8)
        ]
        records.append(_final(9, f"Done with token={secret} part 1. Should I commit?"))
        db = _build(tmp_path, records)
        found = _run(db, monkeypatch)["semantic-permission-to-git"]
        assert found["count"] == 8
        assert len(found["examples"]) == analyzers.MAX_EXAMPLES
        assert len(set(found["examples"])) == len(found["examples"])
        assert all(secret not in e for e in found["examples"])
        assert all("[REDACTED]" in e for e in found["examples"])

    def test_empty_database_returns_empty_list(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = _build(tmp_path, [_tool(1, "Bash", command="ls")])
        assert _run(db, monkeypatch) == {}


class TestRedact:
    @pytest.mark.parametrize(
        "raw",
        [
            "key sk-abcdefghijklmnop1234 end",
            "Authorization: Bearer abc.def.ghi",
            "password = hunter2hunter2",
            "hash " + "a1b2c3d4" * 5,
        ],
    )
    def test_secret_shapes_are_removed(self, raw: str) -> None:
        out = analyzers._redact(raw)
        assert "[REDACTED]" in out
        for leaked in ("sk-abcdefghijklmnop1234", "abc.def.ghi", "hunter2hunter2", "a1b2c3d4" * 5):
            assert leaked not in out

    def test_plain_text_is_unchanged(self) -> None:
        assert analyzers._redact("Should I commit?") == "Should I commit?"


def test_analyze_main_orders_semantic_first_and_prints_tier(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _build(tmp_path, [_final(1, "Done. Should I commit and push?")])
    monkeypatch.setattr(analyzers, "DB_PATH", str(db))
    spec = importlib.util.spec_from_file_location("analyze_main_sem", _HOOK_SCRIPTS / "analyze.py")
    assert spec is not None and spec.loader is not None
    analyze = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyze)
    monkeypatch.setattr(analyze, "DB_PATH", str(db))
    monkeypatch.setattr(sys, "argv", ["analyze.py", "--days", "40000", "--min-occurrences", "1"])
    analyze.main()
    out = capsys.readouterr().out
    assert "Start tier: shadow (target: block)" in out
    assert "Bundled rule: git-gate" in out
    assert "Candidate violation test cases:" in out
    assert os.path.exists(db)
