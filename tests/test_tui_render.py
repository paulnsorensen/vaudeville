"""Tests for OrchestratorTUI rendering — Console(record=True), no timing."""

from __future__ import annotations

import threading


class TestOrchestratorTUIRender:
    def test_render_shows_phase(self) -> None:
        """Phase name appears in rendered output."""
        from rich.console import Console
        from vaudeville.orchestrator_tui import OrchestratorTUI

        console = Console(record=True, width=120)
        tui = OrchestratorTUI(console=console)
        tui.update_phase("generate")

        layout = tui._render()
        console.print(layout)
        text = console.export_text()

        assert "generate" in text

    def test_render_shows_rule_name(self) -> None:
        """Rule name appears in header when set."""
        from rich.console import Console
        from vaudeville.orchestrator_tui import OrchestratorTUI

        console = Console(record=True, width=120)
        tui = OrchestratorTUI(console=console)
        tui.update_phase("design", rule="my-special-rule")

        layout = tui._render()
        console.print(layout)
        text = console.export_text()

        assert "my-special-rule" in text

    def test_render_shows_round_info(self) -> None:
        """Round n/N appears in header when total_rounds > 0."""
        from rich.console import Console
        from vaudeville.orchestrator_tui import OrchestratorTUI

        console = Console(record=True, width=120)
        tui = OrchestratorTUI(console=console)
        tui.update_phase("judge", rule="rule", rnd=2, total_rounds=4)

        layout = tui._render()
        console.print(layout)
        text = console.export_text()

        assert "2/4" in text

    def test_render_shows_verdict(self) -> None:
        """Last verdict appears in header after update_verdict."""
        from rich.console import Console
        from vaudeville.orchestrator_tui import OrchestratorTUI

        console = Console(record=True, width=120)
        tui = OrchestratorTUI(console=console)
        tui.update_verdict("JUDGE_DONE")

        layout = tui._render()
        console.print(layout)
        text = console.export_text()

        assert "JUDGE_DONE" in text

    def test_render_uses_verdict_text_helper(self) -> None:
        """verdict_text from vaudeville.tui drives verdict styling (no inline markup)."""
        from rich.console import Console
        from vaudeville.orchestrator_tui import OrchestratorTUI

        console = Console(record=True, width=120)
        tui = OrchestratorTUI(console=console)
        tui.update_verdict("JUDGE_DONE")

        layout = tui._render()
        console.print(layout)
        ansi = console.export_text(styles=True)

        assert "JUDGE_DONE" in ansi

    def test_render_shows_tail_lines(self) -> None:
        """Tail panel shows appended log lines."""
        from rich.console import Console
        from vaudeville.orchestrator_tui import OrchestratorTUI

        console = Console(record=True, width=120)
        tui = OrchestratorTUI(console=console)
        tui.append_line("ralph output alpha")
        tui.append_line("ralph output beta")

        layout = tui._render()
        console.print(layout)
        text = console.export_text()

        assert "ralph output alpha" in text
        assert "ralph output beta" in text

    def test_render_shows_waiting_when_no_tail(self) -> None:
        """Empty tail shows placeholder text."""
        from rich.console import Console
        from vaudeville.orchestrator_tui import OrchestratorTUI

        console = Console(record=True, width=120)
        tui = OrchestratorTUI(console=console)

        layout = tui._render()
        console.print(layout)
        text = console.export_text()

        assert "waiting" in text

    def test_tail_bounded_to_20_lines(self) -> None:
        """Tail deque evicts oldest entries beyond 20 lines."""
        from rich.console import Console
        from vaudeville.orchestrator_tui import OrchestratorTUI

        tui = OrchestratorTUI(console=Console(record=True, width=120))
        for i in range(30):
            tui.append_line(f"line {i}")

        assert len(tui._tail) == 20
        assert "line 0" not in list(tui._tail)
        assert "line 29" in list(tui._tail)

    def test_enter_exit_context_manager(self) -> None:
        """OrchestratorTUI can be used as a context manager without error."""
        from unittest.mock import patch
        from rich.console import Console
        from vaudeville.orchestrator_tui import OrchestratorTUI

        tui = OrchestratorTUI(console=Console(record=True, width=80))
        with (
            patch.object(tui._live, "start") as mock_start,
            patch.object(tui._live, "stop") as mock_stop,
        ):
            with tui:
                pass
        mock_start.assert_called_once()
        mock_stop.assert_called_once()

    def test_concurrent_updates_serialized_under_lock(self) -> None:
        """append_line + update_verdict from many threads never overlap _live.update."""
        import time
        from unittest.mock import MagicMock
        from rich.console import Console
        from vaudeville.orchestrator_tui import OrchestratorTUI

        tui = OrchestratorTUI(console=Console(record=True, width=80))
        active = 0
        max_active = 0
        tracker = threading.Lock()

        def tracking_update(_renderable: object) -> None:
            nonlocal active, max_active
            with tracker:
                active += 1
                if active > max_active:
                    max_active = active
            time.sleep(0.001)
            with tracker:
                active -= 1

        tui._live = MagicMock()
        tui._live.update.side_effect = tracking_update

        threads: list[threading.Thread] = []
        for i in range(8):
            threads.append(
                threading.Thread(target=lambda i=i: tui.append_line(f"line-{i}"))
            )
            threads.append(
                threading.Thread(target=lambda i=i: tui.update_verdict(f"V-{i}"))
            )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert max_active == 1, (
            f"expected serialized renders, saw {max_active} concurrent"
        )
