"""Rich Live TUI for the orchestrator: status header + streaming log tail.

Peer to ``vaudeville.tui`` — presentation lives outside the orchestrator slice
so the orchestrator stays free of Rich state.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from vaudeville.tui import verdict_text


@dataclass
class _Status:
    phase: str = "starting"
    rule: str = ""
    rnd: int = 0
    total_rounds: int = 0
    last_verdict: str = ""
    started_at: float = field(default_factory=time.time)


class OrchestratorTUI:
    """Rich Live TUI: phase status header + bounded log tail."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._status = _Status()
        self._tail: deque[str] = deque(maxlen=20)
        self._lock = threading.Lock()
        self._live = Live(console=self._console, refresh_per_second=4)

    def __enter__(self) -> OrchestratorTUI:
        self._live.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._live.stop()

    def update_phase(
        self,
        phase: str,
        rule: str = "",
        rnd: int = 0,
        total_rounds: int = 0,
    ) -> None:
        with self._lock:
            self._status.phase = phase
            self._status.rule = rule
            self._status.rnd = rnd
            self._status.total_rounds = total_rounds
            self._live.update(self._render_locked())

    def update_verdict(self, verdict: str) -> None:
        with self._lock:
            self._status.last_verdict = verdict
            self._live.update(self._render_locked())

    def append_line(self, line: str) -> None:
        with self._lock:
            self._tail.append(line.rstrip("\r\n"))
            self._live.update(self._render_locked())

    def _render(self) -> Layout:
        with self._lock:
            return self._render_locked()

    def _render_locked(self) -> Layout:
        header = self._render_header()
        tail = self._render_tail()
        layout = Layout()
        layout.split_column(
            Layout(
                Panel(
                    header,
                    title="[bold]vaudeville orchestrator[/bold]",
                    border_style="cyan",
                ),
                size=4,
            ),
            Layout(Panel(tail, title="output", border_style="dim")),
        )
        return layout

    def _render_header(self) -> Table:
        s = self._status
        elapsed = time.time() - s.started_at
        elapsed_str = f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"

        phase_text = Text(s.phase, style="bold cyan")
        if s.rule:
            phase_text.append(f" › {s.rule}", style="default")

        round_str = f"round {s.rnd}/{s.total_rounds}" if s.total_rounds else ""
        mid = Text()
        if round_str:
            mid.append(round_str)
        if s.last_verdict:
            if mid:
                mid.append("  ")
            mid.append_text(verdict_text(s.last_verdict))

        header = Table.grid(expand=True, padding=(0, 1))
        header.add_column()
        header.add_column(justify="center")
        header.add_column(justify="right", style="dim")
        header.add_row(phase_text, mid, elapsed_str)
        return header

    def _render_tail(self) -> Text:
        lines = list(self._tail)
        return Text(
            "\n".join(lines) if lines else "(waiting for output…)",
            style="dim",
            overflow="fold",
        )
