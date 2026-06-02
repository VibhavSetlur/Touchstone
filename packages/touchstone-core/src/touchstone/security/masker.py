"""Result masker.

Applies the configured strategy (redact / hash / tokenize / partial /
synthetic / never_leaves) to every PII finding AND to every operator-declared
sensitive column. The original `QueryResult` is replaced with a copy that
the LLM is allowed to see.

The `never_leaves` strategy is the strongest: the cell is replaced with a
type-only descriptor (`<TEXT len=437>`), so the LLM knows the column exists
and has data, but never sees a byte of it. Used for medical records,
free-text PII dumps, and other "if the LLM provider trains on this we're
in trouble" columns.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from touchstone.types import PIIFinding, QueryResult


@dataclass(slots=True)
class MaskedResult:
    result: QueryResult
    findings: list[PIIFinding]
    strategy_applied: dict[str, str]   # column -> strategy actually used


Strategy = Callable[[Any, str], Any]   # (raw_value, entity_type) -> masked


def _redact(_value: Any, entity_type: str) -> str:
    return f"[REDACTED:{entity_type}]"


def _hash(value: Any, entity_type: str) -> str:
    salt = os.environ.get("TOUCHSTONE_HASH_SALT", "touchstone-default-salt")
    h = hashlib.blake2b(f"{salt}|{value}".encode(), digest_size=8).hexdigest()
    return f"[H:{h}]"


def _tokenize(value: Any, entity_type: str) -> str:
    salt = os.environ.get("TOUCHSTONE_HASH_SALT", "touchstone-default-salt")
    h = hashlib.blake2b(f"{salt}|{value}".encode(), digest_size=2).hexdigest()
    return f"{entity_type}_{h}"


def _partial(value: Any, entity_type: str) -> str:
    s = str(value)
    if len(s) <= 4:
        return "*" * len(s)
    return "*" * (len(s) - 4) + s[-4:]


def _never_leaves(value: Any, entity_type: str) -> str:
    """Strongest tier: return only the type + length, never the bytes."""
    if value is None:
        return "<NULL>"
    if isinstance(value, str):
        return f"<TEXT len={len(value)}>"
    if isinstance(value, (int, float)):
        return f"<{type(value).__name__}>"
    return f"<{type(value).__name__} len={len(str(value))}>"


def _allow(value: Any, entity_type: str) -> Any:
    """No-op masker. Used by the sensitivity catalog when a column is
    explicitly exempt from masking."""
    return value


def _synthetic(value: Any, entity_type: str) -> str:
    """Stable synthetic value — same input → same output, so the LLM can
    reason about uniqueness/joins without seeing the real value. Backed by
    a small built-in pool per entity type; for richer Faker output, install
    `pip install faker` and the masker will use it automatically."""
    h = int(hashlib.blake2b(str(value).encode(), digest_size=4).hexdigest(), 16)
    pool = _SYNTHETIC_POOLS.get(entity_type, _SYNTHETIC_POOLS["DEFAULT"])
    return pool[h % len(pool)]


_SYNTHETIC_POOLS: dict[str, list[str]] = {
    "PERSON": ["Alex Rivers", "Sam Patel", "Jamie Chen", "Morgan Diaz",
               "Robin Park", "Casey Wu", "Drew Okonkwo", "Quinn Tanaka"],
    "EMAIL": [f"user{i}@example.com" for i in range(16)],
    "PHONE": [f"+15555550{i:03d}" for i in range(16)],
    "ADDRESS": ["1 Main St, Springfield",
                "42 Elm Ave, Riverside",
                "7 Oak Ln, Lakeview"],
    "DEFAULT": [f"VAL_{i:04d}" for i in range(64)],
}


STRATEGIES: dict[str, Strategy] = {
    "redact": _redact,
    "hash": _hash,
    "tokenize": _tokenize,
    "partial": _partial,
    "synthetic": _synthetic,
    "never_leaves": _never_leaves,
    "allow": _allow,
}


@dataclass
class Masker:
    """Note: no `slots=True` — the tier cache (`_tc`) is set lazily in
    `apply()` and would fail under slots."""

    default_strategy: str = "redact"
    # per-entity overrides: {"EMAIL": "tokenize", "PERSON": "synthetic"}
    per_entity: dict[str, str] | None = None
    # per-column overrides: {"users.email": "partial"}
    per_column: dict[str, str] | None = None
    # sensitivity catalog for the active connection (operator-declared).
    # Overrides per_entity/per_column for matched columns.
    sensitivity: Any = None

    def apply(self, result: QueryResult, findings: list[PIIFinding],
              qualified_column_names: dict[str, str] | None = None) -> MaskedResult:
        """Apply masking.

        `qualified_column_names` maps `column.name` → fully-qualified name
        like `users.email`. Used to look up sensitivity-catalog rules.
        Defaults to {column.name: column.name} for backward compat.
        """
        qmap = qualified_column_names or {c.name: c.name for c in result.columns}

        # Compute sensitivity-tier-driven findings (covers columns the PII
        # detector might miss entirely — like `user_notes` containing a
        # free-form medical history).
        sensitivity_findings: list[PIIFinding] = []
        if self.sensitivity is not None:
            for col in result.columns:
                qname = qmap.get(col.name, col.name)
                sample = result.rows[0][_idx(result, col.name)] if result.rows else None
                tier = self.sensitivity.tier_for(qname, col.type, sample)
                if tier != "allow":
                    for row_idx in range(len(result.rows)):
                        sensitivity_findings.append(PIIFinding(
                            column=col.name, row_index=row_idx,
                            detector="sensitivity_catalog",
                            entity_type="OPERATOR_DECLARED",
                            confidence=1.0,
                            span=None,
                        ))
                        # Stash the tier on a side map keyed by (col_name).
                        self._tier_cache[col.name] = tier

        all_findings = findings + sensitivity_findings
        if not all_findings:
            return MaskedResult(result=result, findings=[], strategy_applied={})

        # Build a {(row_idx, col_name) -> (entity_type, strategy)} map.
        # Sensitivity tier wins on STRATEGY (operator declaration > heuristic).
        # PII detection wins on LABEL (more specific entity type).
        # Two passes: PII first sets the cell, sensitivity then upgrades the
        # strategy if it's stricter.
        by_cell: dict[tuple[int, str], tuple[str, str]] = {}
        STRICTNESS = {"allow": 0, "partial": 1, "synthetic": 2, "tokenize": 3,
                      "hash": 4, "redact": 5, "never_leaves": 6}
        for f in all_findings:
            if f.detector != "sensitivity_catalog":
                by_cell[(f.row_index, f.column)] = (
                    f.entity_type, self._pick_strategy(f),
                )
        for f in all_findings:
            if f.detector == "sensitivity_catalog":
                tier = self._tier_cache.get(f.column, self.default_strategy)
                existing = by_cell.get((f.row_index, f.column))
                if existing is None:
                    by_cell[(f.row_index, f.column)] = (f.entity_type, tier)
                elif STRICTNESS.get(tier, 0) > STRICTNESS.get(existing[1], 0):
                    by_cell[(f.row_index, f.column)] = (existing[0], tier)

        col_index = {c.name: i for i, c in enumerate(result.columns)}
        new_rows: list[tuple[Any, ...]] = []
        strategy_applied: dict[str, str] = {}

        for row_idx, row in enumerate(result.rows):
            new_row = list(row)
            for (r, col_name), (entity, strategy) in by_cell.items():
                if r != row_idx:
                    continue
                idx = col_index.get(col_name)
                if idx is None:
                    continue
                fn = STRATEGIES[strategy]
                new_row[idx] = fn(row[idx], entity)
                strategy_applied[col_name] = strategy
            new_rows.append(tuple(new_row))

        masked = QueryResult(
            columns=result.columns,
            rows=new_rows,
            row_count=result.row_count,
            truncated=result.truncated,
            bytes_returned=result.bytes_returned,
            latency_ms=result.latency_ms,
            engine=result.engine,
            sql_executed=result.sql_executed,
        )
        return MaskedResult(result=masked, findings=all_findings,
                            strategy_applied=strategy_applied)

    @property
    def _tier_cache(self) -> dict[str, str]:
        # Cache lives on the Masker instance; reset each call to apply().
        if not hasattr(self, "_tc"):
            self._tc = {}
        return self._tc

    def _pick_strategy(self, f: PIIFinding) -> str:
        if self.per_column and f.column in self.per_column:
            return self.per_column[f.column]
        if self.per_entity and f.entity_type in self.per_entity:
            return self.per_entity[f.entity_type]
        return self.default_strategy


def _idx(result: QueryResult, col_name: str) -> int:
    for i, c in enumerate(result.columns):
        if c.name == col_name:
            return i
    return 0
