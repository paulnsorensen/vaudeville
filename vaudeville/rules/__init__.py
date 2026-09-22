"""Typed rule core: pydantic models for decide and rewrite rules.

Imports only the standard library, pydantic, and yaml; this slice does not
reach into any other part of the package.
"""

from __future__ import annotations

from .actions import Action, ActionName
from .admin import (
    get_draft_rule_names,
    list_rules_with_source,
    locate_all_rule_files,
    locate_rule_file,
    rules_search_path,
    set_tier,
    validate_rule_file,
)
from .cache import load_layered
from .loader import (
    bundled_rules_dir,
    layered_search_path,
    load_rule_file,
    load_rules,
    load_rules_layered,
    project_rules_dir,
    user_rules_dir,
)
from .models import (
    VALID_TIERS,
    DecideRule,
    DecideTestCase,
    RewriteRule,
    Rule,
    RuleSet,
    parse_rule,
)
from .policy import (
    PRIMARY_PRECEDENCE,
    SECONDARY_ACTIONS,
    SILENT_TIER_ALLOWED,
    WARN_DOWNGRADES,
)

__all__ = [
    "Action",
    "ActionName",
    "PRIMARY_PRECEDENCE",
    "SECONDARY_ACTIONS",
    "SILENT_TIER_ALLOWED",
    "WARN_DOWNGRADES",
    "DecideRule",
    "DecideTestCase",
    "RewriteRule",
    "Rule",
    "RuleSet",
    "VALID_TIERS",
    "parse_rule",
    "load_layered",
    "load_rule_file",
    "load_rules",
    "load_rules_layered",
    "layered_search_path",
    "bundled_rules_dir",
    "user_rules_dir",
    "project_rules_dir",
    "get_draft_rule_names",
    "list_rules_with_source",
    "locate_all_rule_files",
    "locate_rule_file",
    "rules_search_path",
    "set_tier",
    "validate_rule_file",
]
