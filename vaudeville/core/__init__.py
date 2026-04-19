from .client import VaudevilleClient
from .paths import find_project_root
from .protocol import (
    ClassifyRequest,
    ClassifyResponse,
    ClassifyResult,
    compute_confidence,
    parse_verdict,
)
from .rules import (
    CHARS_PER_TOKEN,
    EvalCase,
    Rule,
    load_rules,
    load_rules_layered,
    parse_rule,
    prepare_text,
    rules_search_path,
    sanitize_input,
)

__all__ = [
    "CHARS_PER_TOKEN",
    "ClassifyRequest",
    "find_project_root",
    "ClassifyResponse",
    "EvalCase",
    "ClassifyResult",
    "Rule",
    "VaudevilleClient",
    "compute_confidence",
    "load_rules",
    "load_rules_layered",
    "parse_rule",
    "parse_verdict",
    "prepare_text",
    "rules_search_path",
    "sanitize_input",
]
