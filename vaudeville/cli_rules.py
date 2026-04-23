"""Rule management CLI commands for vaudeville."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from vaudeville.core.paths import find_project_root as _core_find_project_root
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

_ACTIVE_TIERS = ("shadow", "warn", "enforce")
_CmdHandler = Callable[[argparse.Namespace], None]


def _find_project_root() -> str:
    return _core_find_project_root() or os.getcwd()


def _rule_names_completer(prefix: str, **_: object) -> list[str]:
    try:
        rules = load_rules_layered()
        return [name for name in rules if name.startswith(prefix)]
    except Exception:
        return []


def cmd_list(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    pairs = list_rules_with_source(project_root)
    if args.tier:
        pairs = [(r, s) for r, s in pairs if r.tier == args.tier]
    if args.event:
        pairs = [(r, s) for r, s in pairs if r.event == args.event]
    pairs.sort(key=lambda x: x[0].name)
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
    if not pairs:
        print("No rules found.")
        return
    hdr = f"{'Name':<35} {'Tier':<10} {'Event':<15} {'Threshold':>9}  Source"
    print(hdr)
    print("-" * len(hdr))
    for rule, source in pairs:
        print(
            f"{rule.name:<35} {rule.tier:<10} {rule.event:<15} {rule.threshold:>9.2f}  {source}"
        )


def _print_rule_human(rule: Rule, path: Path) -> None:
    home = os.path.expanduser("~")
    display_path = str(path).replace(home, "~")
    print(rule.name)
    print(f"  tier: {rule.tier:<12} event: {rule.event:<14} action: {rule.action}")
    print(f"  threshold: {rule.threshold:<7} labels: {rule.labels}")
    print(f"  path: {display_path}")
    print()
    print(f'  message: "{rule.message}"')
    if rule.context:
        print()
        print("  context:")
        for entry in rule.context:
            for k, v in entry.items():
                print(f"    - {k}: {v}")
    if rule.test_cases:
        print()
        counts: dict[str, int] = {}
        for tc in rule.test_cases:
            counts[tc.label] = counts.get(tc.label, 0) + 1
        counts_str = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        print(f"  test_cases: {len(rule.test_cases)} ({counts_str})")
    examples = (rule.examples or rule.candidates)[:2]
    if examples:
        print()
        print("  examples (from prompt):")
        for ex in examples:
            print(f'    [{ex.label}] "{ex.input[:80]}"')


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
                },
                indent=2,
            )
        )
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
            print(f"Rule {args.name!r} exists in multiple locations:")
            for i, p in enumerate(paths, 1):
                print(f"  {i}. {p}")
            raw = input("Delete which? (1/2/all): ").strip().lower()
            if raw == "all":
                pass
            else:
                try:
                    paths = [paths[int(raw) - 1]]
                except (ValueError, IndexError):
                    print("Invalid choice. Aborted.")
                    return
        else:
            resp = input(f"Delete {paths[0]}? [y/N] ").strip().lower()
            if resp != "y":
                print("Aborted.")
                return
    for p in paths:
        p.unlink()
        print(f"Deleted {p}")


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
        print(f"Rule {args.name!r} is already at ceiling (enforce).")
        return
    new_tier = _ACTIVE_TIERS[idx + 1]
    set_tier(args.name, new_tier, project_root)
    print(f"Promoted {args.name!r}: {current} → {new_tier}")


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
        print(f"Rule {args.name!r} is already at floor (shadow).")
        return
    new_tier = _ACTIVE_TIERS[idx - 1]
    set_tier(args.name, new_tier, project_root)
    print(f"Demoted {args.name!r}: {current} → {new_tier}")


def cmd_disable(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    try:
        path = locate_rule_file(args.name, project_root)
    except FileNotFoundError:
        print(f"Rule {args.name!r} not found.", file=sys.stderr)
        sys.exit(1)
    content = path.read_text()
    current = _load_tier(path)
    if current == "disabled":
        print(f"Rule {args.name!r} is already disabled.")
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
    print(
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
    current = _load_tier(path)
    if current != "disabled":
        print(f"Rule {args.name!r} is already enabled (tier: {current!r}).")
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
    print(f"Enabled {args.name!r} (tier: {restore!r}).")


def cmd_path(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    try:
        path = locate_rule_file(args.name, project_root)
    except FileNotFoundError:
        print(f"Rule {args.name!r} not found.", file=sys.stderr)
        sys.exit(1)
    print(path)


def cmd_validate(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    if args.name:
        try:
            path = locate_rule_file(args.name, project_root)
            load_rule_file(path)
            print(f"OK      {args.name}")
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
                print(f"OK      {name}")
            except Exception as exc:
                print(f"INVALID {name}: {exc}")
                errors += 1
    if errors:
        sys.exit(1)


def cmd_completion(args: argparse.Namespace) -> None:
    if args.shell in ("bash", "zsh"):
        print('eval "$(register-python-argcomplete vaudeville)"')
    elif args.shell == "fish":
        print("register-python-argcomplete --shell fish vaudeville | source")


def attach_rule_parsers(sub: Any) -> None:
    """Register all rule management subparsers with argcomplete support."""

    def _name(p: Any) -> Any:
        action = p.add_argument("name", help="Rule name")
        action.completer = _rule_names_completer
        return action

    lp = sub.add_parser("list", help="List all rules")
    lp.add_argument("--tier", choices=VALID_TIERS, help="Filter by tier")
    lp.add_argument("--event", help="Filter by event type")
    lp.add_argument("--json", action="store_true", help="Output raw JSON")

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
    """Dispatch to a rule management command. Returns True if the command was handled."""
    handler = _RULE_COMMANDS.get(args.command)
    if handler is None:
        return False
    handler(args)
    return True
