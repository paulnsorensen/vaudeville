"""Rich rendering for `vaudeville stats` aggregated event output."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text

from vaudeville.tui import latency_text, styled_table


def render_rules_table(con: Console, rules: dict[str, Any], total: int) -> None:
    table = styled_table(
        "Per-Rule Breakdown",
        caption=f"{total} classifications",
    )
    table.add_column("Rule", justify="left")
    for col in ("Total", "Violations", "Pass %", "Avg ms", "p50 ms", "p95 ms"):
        table.add_column(col, justify="right")

    for name, data in rules.items():
        pass_rate = data["pass_rate"]
        style = "green" if pass_rate >= 90 else "yellow" if pass_rate >= 50 else "red"
        table.add_row(
            name,
            str(data["total"]),
            str(data["violations"]),
            Text(f"{pass_rate:.1f}%", style=style),
            latency_text(data["avg_latency_ms"]),
            latency_text(data["p50_latency_ms"]),
            latency_text(data["p95_latency_ms"]),
        )
    con.print(table)


def render_latency_summary(con: Console, lat: dict[str, Any]) -> None:
    summary = Text.assemble(
        "Overall latency \u2014 p50: ",
        (f"{lat['p50_ms']:.1f}ms", "bold"),
        "   p95: ",
        (f"{lat['p95_ms']:.1f}ms", "bold"),
        "   mean: ",
        (f"{lat['mean_ms']:.1f}ms", "bold"),
    )
    con.print(summary)
    con.print()


def render_histogram(con: Console, histogram: dict[str, int]) -> None:
    hist_table = styled_table("Latency Histogram")
    hist_table.add_column("Bucket", justify="right")
    hist_table.add_column("Count", justify="right")
    hist_table.add_column("Bar")

    max_count = max(histogram.values()) if histogram else 1
    bar_width = 40
    for bucket, count in histogram.items():
        bar_len = int((count / max_count) * bar_width) if max_count else 0
        hist_table.add_row(bucket, str(count), "\u2588" * bar_len)
    con.print(hist_table)


def print_stats_human(result: dict[str, Any], console: Console) -> None:
    total = result["total"]
    if total == 0:
        console.print("No events recorded yet.")
        return

    console.rule("Vaudeville Stats", style="bold cyan")
    rules = result["rules"]
    if rules:
        render_rules_table(console, rules, total)
    lat = result["latency"]
    render_latency_summary(console, lat)
    render_histogram(console, lat["histogram"])
