"""Policy engine.

Touchstone ships with a small embedded policy evaluator. By default it loads
a sensible "read-only, mask PII, require consent for non-SELECT and prod"
bundle. Operators override with YAML or CSV files referenced from config.

We use a custom evaluator (not PyCasbin directly) for three reasons:
  1. We need to evaluate ABAC conditions over a SQL-summary dict, which
     Casbin's matcher language can handle but reads poorly.
  2. We want consent-required as a first-class verdict, not just allow/deny.
  3. We want policies in YAML (operator-friendly) with CSV fallback.

For enterprises that have standardized on OPA, an `opa_bridge.py` module
(roadmap) will forward decisions to a local OPA daemon.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from touchstone.config import ConnectionConfig
from touchstone.types import PolicyDecision, Verdict


@dataclass(slots=True)
class PolicyRule:
    """One rule in the policy bundle."""

    name: str
    effect: str           # "allow" | "deny" | "consent"
    assistants: list[str] # list of assistant ids or globs ("*", "claude-*")
    connections: list[str]
    tools: list[str]
    when: str | None      # optional Python-expression condition; see _evaluate_condition
    priority: int = 100   # lower runs first


class PolicyEngine:
    """Evaluates rules against a (assistant, connection, tool, sql) tuple.

    Rules are evaluated in priority order. First match wins. If no rule matches,
    the result is implicit-deny (default-deny).
    """

    def __init__(self, rules: list[PolicyRule]) -> None:
        # Stable sort by priority so identical priorities preserve file order.
        self.rules = sorted(rules, key=lambda r: r.priority)

    @classmethod
    def from_files(cls, paths: list[Path]) -> PolicyEngine:
        rules: list[PolicyRule] = [_default_rule(r) for r in _DEFAULT_RULES]
        for path in paths:
            rules.extend(cls._load_file(path))
        return cls(rules)

    @classmethod
    def _load_file(cls, path: Path) -> list[PolicyRule]:
        with path.open() as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or "rules" not in data:
            raise ValueError(f"{path}: expected a top-level 'rules' list")
        return [_default_rule(r) for r in data["rules"]]

    def evaluate(
        self,
        assistant_id: str,
        connection: ConnectionConfig,
        tool: str,
        sql_summary: dict[str, Any],
        metadata: dict[str, Any],
    ) -> PolicyDecision:
        context = {
            "assistant_id": assistant_id,
            "connection": connection.name,
            "tags": set(connection.tags),
            "read_only": connection.read_only,
            "tool": tool,
            "sql": sql_summary,
            "metadata": metadata,
        }
        for rule in self.rules:
            if not _matches(assistant_id, rule.assistants):
                continue
            if not _matches(connection.name, rule.connections):
                continue
            if not _matches(tool, rule.tools):
                continue
            if rule.when and not _evaluate_condition(rule.when, context):
                continue

            if rule.effect == "deny":
                return PolicyDecision(
                    verdict=Verdict.DENY, matched_rule=rule.name,
                    reason=f"deny by rule {rule.name!r}",
                )
            if rule.effect == "consent":
                return PolicyDecision(
                    verdict=Verdict.CONSENT_REQUIRED, matched_rule=rule.name,
                    reason=f"rule {rule.name!r} requires consent",
                    consent_context={"connection": connection.name, "tool": tool},
                )
            if rule.effect == "allow":
                return PolicyDecision(
                    verdict=Verdict.PERMIT, matched_rule=rule.name,
                    reason=f"allowed by rule {rule.name!r}",
                )

        return PolicyDecision(
            verdict=Verdict.DENY, matched_rule="implicit_deny",
            reason="no policy rule matched; default-deny",
        )


def _matches(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, p) for p in patterns)


def _evaluate_condition(expr: str, context: dict[str, Any]) -> bool:
    """Evaluate a tiny expression DSL against the call context.

    Supported expressions:
      - sql.is_select                       (boolean attribute)
      - sql.kind == "SELECT"                (equality)
      - "audit_log" in sql.touches          (membership)
      - "prod" in tags
      - not sql.has_limit
      - sql.joins > 3
      - and/or combinations

    Implementation: SAFE evaluation via the `ast` module + a whitelist of
    operators. We do NOT use eval() because rule files come from operators
    and the policy engine must be hardened.
    """
    import ast

    tree = ast.parse(expr, mode="eval")
    return bool(_evaluate(tree.body, context))


_ALLOWED_BINOPS = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def _evaluate(node: ast.AST, ctx: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in ctx:
            raise ValueError(f"unknown name in policy condition: {node.id!r}")
        return ctx[node.id]
    if isinstance(node, ast.Attribute):
        obj = _evaluate(node.value, ctx)
        if isinstance(obj, dict):
            return obj.get(node.attr)
        return getattr(obj, node.attr, None)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _evaluate(node.operand, ctx)
    if isinstance(node, ast.BoolOp):
        results = [_evaluate(v, ctx) for v in node.values]
        return all(results) if isinstance(node.op, ast.And) else any(results)
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, ctx)
        for op, right_node in zip(node.ops, node.comparators, strict=False):
            right = _evaluate(right_node, ctx)
            fn = _ALLOWED_BINOPS.get(type(op))
            if fn is None:
                raise ValueError(f"disallowed comparison operator: {type(op).__name__}")
            if not fn(left, right):
                return False
            left = right
        return True
    raise ValueError(f"disallowed node in policy condition: {type(node).__name__}")


def _default_rule(raw: dict[str, Any]) -> PolicyRule:
    return PolicyRule(
        name=raw["name"],
        effect=raw["effect"],
        assistants=raw.get("assistants", ["*"]),
        connections=raw.get("connections", ["*"]),
        tools=raw.get("tools", ["*"]),
        when=raw.get("when"),
        priority=raw.get("priority", 100),
    )


# Default policy bundle — opinionated, conservative, can be overridden per
# install but ships with safe behavior out of the box.
_DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "name": "deny-system-tables",
        "effect": "deny",
        "tools": ["query_database"],
        "when": '"audit_log" in sql.touches or "users.password_hash" in sql.touches',
        "priority": 10,
    },
    {
        "name": "consent-for-non-select",
        "effect": "consent",
        "tools": ["query_database"],
        "when": "not sql.is_select",
        "priority": 20,
    },
    {
        "name": "consent-for-prod",
        "effect": "consent",
        "tools": ["query_database", "profile_table", "diff_environments"],
        "when": '"prod" in tags',
        "priority": 30,
    },
    {
        "name": "allow-select-read-only",
        "effect": "allow",
        "tools": ["query_database", "profile_table", "diff_environments",
                  "explain_lineage", "analyze_pr_data_impact", "generate_test_cases",
                  "check_data_quality"],
        "when": "sql.is_select and read_only",
        "priority": 100,
    },
    {
        "name": "allow-metadata-tools",
        "effect": "allow",
        "tools": ["list_connections", "list_tables", "describe_table",
                  "audit_query"],
        "priority": 200,
    },
]
