from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.table import Table
from rich.text import Text
from vaudeville.core.paths import find_project_root as _core_find_project_root
from vaudeville.core.protocol import CLASSIFY_MAX_TOKENS
from vaudeville.core.rules import (
    VALID_TIERS,
    Rule,
    load_rule_file,
    load_rules_layered,
    list_rules_with_source,
    locate_all_rule_files,
    locate_rule_file,
    rules_search_path,
    set_tier,
)
from vaudeville.tui import styled_table, tier_text

_ACTIVE_TIERS = ("shadow", "warn", "enforce")
_CmdHandler = Callable[[argparse.Namespace], None]
_LIST_POLL_INTERVAL = 0.5
_console = Console()


def _find_project_root() -> str:
    return _core_find_project_root() or os.getcwd()


def _human_prompt(prompt: str) -> str:
    lines = prompt.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("now classify"):
            return "\n".join(lines[:i]).rstrip()
    return prompt.rstrip()


def _rule_names_completer(prefix: str, **_: object) -> list[str]:
    try:
        rules = load_rules_layered()
        return [name for name in rules if name.startswith(prefix)]
    except Exception:
        return []


def _positive_poll_interval(value: str) -> float:
    interval = float(value)
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("poll interval must be > 0")
    return interval


def cmd_list(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    pairs = _list_rule_pairs(project_root, args.tier, args.event)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": r.name,
                        "tier": r.tier,
                        "event": r.event,
                        "threshold": r.threshold,
                        "source": s,
                    }
                    for r, s in pairs
                ],
                indent=2,
            )
        )
        return
    if args.live:
        _run_list_live(
            project_root=project_root,
            tier=args.tier,
            event=args.event,
            poll_interval=args.poll_interval,
        )
        return
    _print_list_table(_console, pairs)


def _list_rule_pairs(
    project_root: str, tier: str | None, event: str | None
) -> list[tuple[Rule, str]]:
    pairs = list_rules_with_source(project_root)
    if tier:
        pairs = [(r, s) for r, s in pairs if r.tier == tier]
    if event:
        pairs = [(r, s) for r, s in pairs if r.event == event]
    pairs.sort(key=lambda x: x[0].name)
    return pairs


def _build_list_table(pairs: list[tuple[Rule, str]]) -> Table:
    table = styled_table(
        "Vaudeville Rules",
        caption=f"{len(pairs)} rule{'s' if len(pairs) != 1 else ''}",
    )
    table.add_column("Name", min_width=20, overflow="fold")
    table.add_column("Tier", no_wrap=True)
    table.add_column("Event", no_wrap=True)
    table.add_column("Threshold", justify="right", no_wrap=True)
    table.add_column("Source", ratio=1, overflow="fold")
    for rule, source in pairs:
        table.add_row(
            rule.name,
            tier_text(rule.tier),
            rule.event,
            f"{rule.threshold:.2f}",
            source,
        )
    return table


def _print_list_table(console: Console, pairs: list[tuple[Rule, str]]) -> None:
    if not pairs:
        console.print("No rules found.")
        return
    console.print(_build_list_table(pairs))


def _add_list_subparser(sub: Any) -> None:
    lp = sub.add_parser("list", help="List all rules")
    lp.add_argument("--tier", choices=VALID_TIERS, help="Filter by tier")
    lp.add_argument("--event", help="Filter by event type")
    output_group = lp.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", help="Output raw JSON")
    output_group.add_argument(
        "--live",
        action="store_true",
        help="Continuously refresh rule list like the watch TUI",
    )
    lp.add_argument(
        "--poll-interval",
        type=_positive_poll_interval,
        default=_LIST_POLL_INTERVAL,
        help=f"Seconds between refreshes in --live mode (default: {_LIST_POLL_INTERVAL})",
    )


def _run_list_live(
    project_root: str,
    tier: str | None,
    event: str | None,
    poll_interval: float,
) -> None:
    from rich.live import Live

    pairs = _list_rule_pairs(project_root, tier, event)
    with Live(_build_list_table(pairs), refresh_per_second=4, console=_console) as live:
        try:
            while True:
                pairs = _list_rule_pairs(project_root, tier, event)
                live.update(_build_list_table(pairs))
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            return


def _build_show_summary_table(rule: Rule, path: Path) -> Table:
    home = os.path.expanduser("~")
    display_path = str(path).replace(home, "~")
    table = styled_table(rule.name)
    table.add_column("Field", no_wrap=True)
    table.add_column("Value", overflow="fold")
    table.add_row("tier", tier_text(rule.tier))
    table.add_row("event", rule.event)
    table.add_row("action", rule.action)
    table.add_row("threshold", f"{rule.threshold:.2f}")
    table.add_row("labels", ", ".join(rule.labels))
    table.add_row("path", display_path)
    return table


def _print_rule_human(rule: Rule, path: Path) -> None:
    _console.print(_build_show_summary_table(rule, path))
    _console.print()
    _console.print(Text("Message", style="bold"))
    _console.print(f'"{rule.message}"')
    _console.print()
    _console.print(Text("Prompt", style="bold"))
    _console.print(_human_prompt(rule.prompt))


def _show_json(rule: Rule, path: Path) -> None:
    sample_prompt, prefix_len = rule.split_prompt("{text}", "{context}")
    print(
        json.dumps(
            {
                "name": rule.name,
                "tier": rule.tier,
                "event": rule.event,
                "threshold": rule.threshold,
                "action": rule.action,
                "message": rule.message,
                "labels": rule.labels,
                "test_case_count": len(rule.test_cases),
                "path": str(path),
                "context": rule.context,
                "prompt": rule.prompt,
                "prompt_example": sample_prompt,
                "classify_max_tokens": CLASSIFY_MAX_TOKENS,
                "payload_example": {
                    "op": "classify",
                    "prompt": sample_prompt,
                    "rule": rule.name,
                    "prefix_len": prefix_len,
                    "tier": rule.tier,
                    "input_text": "{raw input before prompt formatting}",
                },
            },
            indent=2,
        )
    )


def cmd_show(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    try:
        path = locate_rule_file(args.name, project_root)
    except FileNotFoundError:
        print(f"Rule {args.name!r} not found.", file=sys.stderr)
        sys.exit(1)
    rule = load_rule_file(path)
    if rule is None:
        print(f"Rule {args.name!r} is a draft.", file=sys.stderr)
        sys.exit(1)
    if args.json:
        _show_json(rule, path)
        return
    _print_rule_human(rule, path)


def cmd_delete(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    paths = locate_all_rule_files(args.name, project_root)
    if not paths:
        print(f"Rule {args.name!r} not found.", file=sys.stderr)
        sys.exit(1)
    if not args.yes:
        if not sys.stdin.isatty():
            print(
                f"Rule {args.name!r}: pass --yes for non-interactive delete.",
                file=sys.stderr,
            )
            sys.exit(1)
        if len(paths) > 1:
            _console.print(f"Rule {args.name!r} exists in multiple locations:")
            for i, p in enumerate(paths, 1):
                _console.print(f"  {i}. {p}")
            raw = input(f"Delete which? (1-{len(paths)}/all): ").strip().lower()
            if raw == "all":
                pass
            else:
                try:
                    idx = int(raw) - 1
                    if not (0 <= idx < len(paths)):
                        raise IndexError
                    paths = [paths[idx]]
                except (ValueError, IndexError):
                    _console.print("Invalid choice. Aborted.")
                    return
        else:
            resp = input(f"Delete {paths[0]}? [y/N] ").strip().lower()
            if resp != "y":
                _console.print("Aborted.")
                return
    for p in paths:
        p.unlink()
        _console.print(f"Deleted {p}")


def _load_tier(path: Path) -> str:
    content = path.read_text()
    m = re.search(r"^tier:\s*(\S+)", content, re.MULTILINE)
    return m.group(1) if m else "shadow"


def cmd_promote(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    try:
        path = locate_rule_file(args.name, project_root)
    except FileNotFoundError:
        print(f"Rule {args.name!r} not found.", file=sys.stderr)
        sys.exit(1)
    current = _load_tier(path)
    if current not in _ACTIVE_TIERS:
        print(
            f"Rule {args.name!r} is {current!r}; use 'enable' to restore it first.",
            file=sys.stderr,
        )
        sys.exit(1)
    idx = _ACTIVE_TIERS.index(current)
    if idx == len(_ACTIVE_TIERS) - 1:
        _console.print(f"Rule {args.name!r} is already at ceiling (enforce).")
        return
    new_tier = _ACTIVE_TIERS[idx + 1]
    set_tier(args.name, new_tier, project_root)
    _console.print(f"Promoted {args.name!r}: {current} → {new_tier}")


def cmd_demote(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    try:
        path = locate_rule_file(args.name, project_root)
    except FileNotFoundError:
        print(f"Rule {args.name!r} not found.", file=sys.stderr)
        sys.exit(1)
    current = _load_tier(path)
    if current not in _ACTIVE_TIERS:
        print(
            f"Rule {args.name!r} is {current!r}; use 'disable' instead.",
            file=sys.stderr,
        )
        sys.exit(1)
    idx = _ACTIVE_TIERS.index(current)
    if idx == 0:
        _console.print(f"Rule {args.name!r} is already at floor (shadow).")
        return
    new_tier = _ACTIVE_TIERS[idx - 1]
    set_tier(args.name, new_tier, project_root)
    _console.print(f"Demoted {args.name!r}: {current} → {new_tier}")


def cmd_disable(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    try:
        path = locate_rule_file(args.name, project_root)
    except FileNotFoundError:
        print(f"Rule {args.name!r} not found.", file=sys.stderr)
        sys.exit(1)
    content = path.read_text()
    m = re.search(r"^tier:\s*(\S+)", content, re.MULTILINE)
    current = m.group(1) if m else "shadow"
    if current == "disabled":
        _console.print(f"Rule {args.name!r} is already disabled.")
        return
    new_content, count = re.subn(
        r"^tier:\s*\S+", "tier: disabled", content, flags=re.MULTILINE
    )
    if count == 0:
        sep = "" if not content or content.endswith("\n") else "\n"
        new_content = content + sep + "tier: disabled\n"
    if not new_content.endswith("\n"):
        new_content += "\n"
    new_content += f"# previous-tier: {current}\n"
    path.write_text(new_content)
    _console.print(
        f"Disabled {args.name!r} (was {current!r}). Use 'vaudeville enable {args.name}' to restore."
    )


def cmd_enable(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    try:
        path = locate_rule_file(args.name, project_root)
    except FileNotFoundError:
        print(f"Rule {args.name!r} not found.", file=sys.stderr)
        sys.exit(1)
    content = path.read_text()
    m = re.search(r"^tier:\s*(\S+)", content, re.MULTILINE)
    current = m.group(1) if m else "shadow"
    if current != "disabled":
        _console.print(f"Rule {args.name!r} is already enabled (tier: {current!r}).")
        return
    pm = re.search(r"^# previous-tier:\s*(\S+)", content, re.MULTILINE)
    restore = pm.group(1) if pm else "shadow"
    new_content = re.sub(
        r"^tier:\s*\S+", f"tier: {restore}", content, flags=re.MULTILINE
    )
    new_content = re.sub(
        r"^# previous-tier:[^\n]*\n?", "", new_content, flags=re.MULTILINE
    )
    path.write_text(new_content)
    _console.print(f"Enabled {args.name!r} (tier: {restore!r}).")


def cmd_path(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    try:
        path = locate_rule_file(args.name, project_root)
    except FileNotFoundError:
        print(f"Rule {args.name!r} not found.", file=sys.stderr)
        sys.exit(1)
    _console.print(str(path))


def cmd_validate(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    if args.name:
        try:
            path = locate_rule_file(args.name, project_root)
            load_rule_file(path)
            _console.print(f"OK      {args.name}")
        except FileNotFoundError:
            print(f"NOT FOUND  {args.name}", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            print(f"INVALID {args.name}: {exc}", file=sys.stderr)
            sys.exit(1)
        return
    errors = 0
    for rules_dir in rules_search_path(project_root):
        for filename in sorted(os.listdir(rules_dir)):
            if not (filename.endswith(".yaml") or filename.endswith(".yml")):
                continue
            p = Path(rules_dir) / filename
            name = filename.rsplit(".", 1)[0]
            try:
                load_rule_file(p)
                _console.print(f"OK      {name}")
            except Exception as exc:
                print(f"INVALID {name}: {exc}", file=sys.stderr)
                errors += 1
    if errors:
        sys.exit(1)


def cmd_completion(args: argparse.Namespace) -> None:
    if args.shell in ("bash", "zsh"):
        print('eval "$(register-python-argcomplete vaudeville)"')
    elif args.shell == "fish":
        print("register-python-argcomplete --shell fish vaudeville | source")


def attach_rule_parsers(sub: Any) -> None:
    def _name(p: Any) -> Any:
        action = p.add_argument("name", help="Rule name")
        action.completer = _rule_names_completer
        return action

    _add_list_subparser(sub)

    sp = sub.add_parser("show", help="Show rule details")
    _name(sp)
    sp.add_argument("--json", action="store_true", help="Output raw JSON")

    dp = sub.add_parser("delete", help="Delete a rule file")
    _name(dp)
    dp.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    for cmd, help_txt in (
        ("promote", "Promote rule one tier (shadow→warn→enforce)"),
        ("demote", "Demote rule one tier (enforce→warn→shadow)"),
        ("enable", "Restore a disabled rule to its previous tier"),
        ("disable", "Disable a rule (saves previous tier for restore)"),
        ("path", "Print the resolved path of a rule file"),
    ):
        _name(sub.add_parser(cmd, help=help_txt))

    vp = sub.add_parser(
        "validate", help="Validate rule YAML (all rules if name omitted)"
    )
    vp.add_argument("name", nargs="?", help="Rule name")

    cp = sub.add_parser("completion", help="Print shell completion setup command")
    cp.add_argument("shell", choices=["bash", "zsh", "fish"])


_RULE_COMMANDS: dict[str, _CmdHandler] = {
    "list": cmd_list,
    "show": cmd_show,
    "delete": cmd_delete,
    "promote": cmd_promote,
    "demote": cmd_demote,
    "enable": cmd_enable,
    "disable": cmd_disable,
    "path": cmd_path,
    "validate": cmd_validate,
    "completion": cmd_completion,
}


def dispatch_rule_command(args: argparse.Namespace) -> bool:
    """Returns True if handled, False if command unknown."""
    handler = _RULE_COMMANDS.get(args.command)
    if handler is None:
        return False
    handler(args)
    return True
