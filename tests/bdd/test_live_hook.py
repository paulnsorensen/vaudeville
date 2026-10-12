from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from anthropic.types import Message
from pydantic import TypeAdapter
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("live_hook.feature")

MODEL = "claude-haiku-4-5"
RULE = "restricted-write"
PLUGIN_ROOT = Path(
    os.environ.get("VAUDEVILLE_BDD_PLUGIN_ROOT", Path(__file__).resolve().parents[2])
)
DAEMON_CODE = """
import os
from vaudeville.server import DaemonConfig, EventLogger, VaudevilleDaemon
root = os.environ["VAUDEVILLE_BDD_RUNTIME"]
config = DaemonConfig(
    socket_path=os.environ["VAUDEVILLE_SOCKET"],
    pid_file=root + "/daemon.pid",
    plugin_root=os.environ["CLAUDE_PLUGIN_ROOT"],
    version_file=root + "/version",
)
logger = EventLogger(logs_dir=os.environ["VAUDEVILLE_BDD_LOGS"])
VaudevilleDaemon(config, event_logger=logger).serve()
"""


class Provider(ThreadingHTTPServer):
    requests: list[dict[str, Any]]
    errors: list[str]


class ProviderHandler(BaseHTTPRequestHandler):
    server: Provider

    def do_POST(self) -> None:
        try:
            assert self.path == "/v1/messages?beta=true", self.path
            assert self.headers["x-api-key"] == "bdd-fake-key"
            assert self.headers["anthropic-version"]
            size = int(self.headers["content-length"])
            request = cast(dict[str, Any], json.loads(self.rfile.read(size)))
            assert request["model"] == MODEL
            assert request["temperature"] == 0
            assert request["max_tokens"] > 0
            assert "Restricted writes are a violation" in json.dumps(request["system"])
            assert request["messages"] == [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "<<<HOOK-DATA\nsecret\nHOOK-DATA>>>"}],
                }
            ]
            tools = request["tools"]
            assert len(tools) == 1
            tool = tools[0]
            assert tool["input_schema"]["properties"]["outcome"]["enum"] == [
                "violation",
                "clean",
            ]
            self.server.requests.append(request)
            response = {
                "id": "msg_bdd_1",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_bdd_1",
                        "name": tool["name"],
                        "input": {"outcome": "violation"},
                    }
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 4},
            }
            TypeAdapter(Message).validate_python(response)
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self.server.errors.append(repr(exc))
            self.send_error(500, repr(exc))

    def log_message(self, format: str, *args: object) -> None:
        pass


@dataclass
class LiveHook:
    home: Path
    project: Path
    runtime: Path
    env: dict[str, str]
    provider: Provider
    daemon: subprocess.Popen[str]
    results: list[subprocess.CompletedProcess[str]] = field(default_factory=list)

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(Path(sys.executable).parent / "vaudeville"), *args],
            cwd=self.project,
            env=self.env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def hook(self) -> subprocess.CompletedProcess[str]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "restricted.txt", "content": "secret"},
            "cwd": str(self.project),
        }
        result = subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "hooks" / "runner.py"), "--harness", "claude-code"],
            cwd=self.project,
            env=self.env,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.results.append(result)
        assert self.provider.errors == []
        assert self.daemon.poll() is None
        return result

    def events(self) -> list[dict[str, Any]]:
        path = self.home / ".vaudeville" / "logs" / "events.jsonl"
        return [cast(dict[str, Any], json.loads(line)) for line in path.read_text().splitlines()]

    def stats(self) -> dict[str, Any]:
        result = self.cli("stats", "--json")
        assert result.returncode == 0, result.stderr
        return cast(dict[str, Any], json.loads(result.stdout))


def wait_for_daemon(daemon: subprocess.Popen[str], socket_path: str) -> None:
    deadline = time.monotonic() + 10
    while True:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(socket_path) == 0:
                return
        if daemon.poll() is not None or time.monotonic() >= deadline:
            if daemon.poll() is None:
                daemon.terminate()
            stdout, stderr = daemon.communicate(timeout=5)
            pytest.fail(f"daemon did not start: {daemon.returncode}; {stdout}; {stderr}")
        time.sleep(0.02)


@pytest.fixture
def live_hook() -> Iterator[LiveHook]:
    with tempfile.TemporaryDirectory(prefix="v-bdd-") as directory:
        root = Path(directory)
        home = root / "home"
        project = root / "project"
        runtime = root / "run"
        for path in (home, project, runtime):
            path.mkdir(mode=0o700)
        subprocess.run(
            ["git", "init", "--quiet", str(project)],
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            },
            check=True,
            capture_output=True,
            timeout=10,
        )
        provider = Provider(("127.0.0.1", 0), ProviderHandler)
        provider.requests = []
        provider.errors = []
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        daemon: subprocess.Popen[str] | None = None
        try:
            env = {
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT),
                "VAUDEVILLE_SOCKET": str(runtime / "daemon.sock"),
                "VAUDEVILLE_BDD_RUNTIME": str(runtime),
                "VAUDEVILLE_BDD_LOGS": str(home / ".vaudeville" / "logs"),
                "ANTHROPIC_API_KEY": "bdd-fake-key",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "NO_COLOR": "1",
                "PYDANTIC_AI_NO_BANNER": "1",
            }
            config = home / ".vaudeville" / "config"
            config.parent.mkdir()
            config.write_text(
                yaml.safe_dump(
                    {
                        "default_model": f"anthropic:{MODEL}",
                        "providers": {
                            "anthropic": {
                                "key_env": "ANTHROPIC_API_KEY",
                                "base_url": f"http://127.0.0.1:{provider.server_port}",
                            }
                        },
                    }
                )
            )
            daemon = subprocess.Popen(
                [sys.executable, "-c", DAEMON_CODE],
                cwd=project,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            wait_for_daemon(daemon, env["VAUDEVILLE_SOCKET"])
            yield LiveHook(home, project, runtime, env, provider, daemon)
        finally:
            if daemon is not None:
                if daemon.poll() is None:
                    daemon.terminate()
                try:
                    daemon.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.communicate(timeout=5)
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=5)


@given(parsers.parse('a live rule at tier "{tier}"'))
def live_rule(live_hook: LiveHook, tier: str) -> None:
    rules = live_hook.home / ".vaudeville" / "rules"
    rules.mkdir()
    (rules / f"{RULE}.yaml").write_text(
        yaml.safe_dump(
            {
                "type": "decide",
                "name": RULE,
                "event": "PreToolUse",
                "matcher": "Write",
                "tier": tier,
                "prompt": "Restricted writes are a violation: {text}",
                "outcomes": ["violation", "clean"],
                "on": {"violation": "block"},
            }
        )
    )


@when("the assistant calls a restricted tool")
def call_hook(live_hook: LiveHook) -> None:
    live_hook.hook()


@when("I promote the live rule")
def promote_rule(live_hook: LiveHook) -> None:
    result = live_hook.cli("promote", RULE)
    assert result.returncode == 0, result.stderr
    assert live_hook.cli("show", RULE, "--json").returncode == 0


@when("I disable the live rule")
def disable_rule(live_hook: LiveHook) -> None:
    result = live_hook.cli("disable", RULE)
    assert result.returncode == 0, result.stderr


@then("the harness denies the tool call")
def denied(live_hook: LiveHook) -> None:
    result = live_hook.results[-1]
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "violation",
        }
    }
    assert len(live_hook.provider.requests) == len(live_hook.results)


@then("the harness warns without denying the tool call")
def warned(live_hook: LiveHook) -> None:
    result = live_hook.results[-1]
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"systemMessage": "violation"}
    assert live_hook.events()[0]["action"] == "warn"
    assert len(live_hook.provider.requests) == 1


@then("the violation is recorded in statistics")
def violation_stats(live_hook: LiveHook) -> None:
    events = live_hook.events()
    assert len(events) == 1
    assert {key: events[0][key] for key in ("rule", "outcome", "action", "tier", "model")} == {
        "rule": RULE,
        "outcome": "violation",
        "action": "block",
        "tier": "block",
        "model": f"anthropic:{MODEL}",
    }
    stats = live_hook.stats()
    assert stats["total"] == 1
    assert stats["actions"] == {"block": 1}
    rule_stats = stats["rules"][RULE]
    assert {key: rule_stats[key] for key in ("total", "violations", "dropped", "pass_rate")} == {
        "total": 1,
        "violations": 1,
        "dropped": 0,
        "pass_rate": 0.0,
    }


@then("the harness allows the tool call without another evaluation")
def disabled_allows(live_hook: LiveHook) -> None:
    result = live_hook.results[-1]
    assert result.returncode == 0
    assert result.stdout == "\n"
    assert len(live_hook.provider.requests) == 2
    assert [(event["tier"], event["action"]) for event in live_hook.events()] == [
        ("warn", "warn"),
        ("block", "block"),
    ]
    stats = live_hook.stats()
    assert stats["total"] == 2
    assert stats["actions"] == {"warn": 1, "block": 1}


@then("the same daemon served every tool call")
def daemon_unchanged(live_hook: LiveHook) -> None:
    assert live_hook.daemon.poll() is None
    assert int((live_hook.runtime / "daemon.pid").read_text()) == live_hook.daemon.pid
    assert len(live_hook.results) == 3
