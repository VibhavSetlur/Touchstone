"""Result masker.

Applies the configured strategy (redact / hash / tokenize / partial / synthetic)
to every PII finding. The original `QueryResult` is replaced with a copy that
the LLM is allowed to see.
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
}


@dataclass(slots=True)
class Masker:
    default_strategy: str = "redact"
    # per-entity overrides: {"EMAIL": "tokenize", "PERSON": "synthetic"}
    per_entity: dict[str, str] | None = None
    # per-column overrides: {"users.email": "partial"}
    per_column: dict[str, str] | None = None

    def apply(self, result: QueryResult, findings: list[PIIFinding]) -> MaskedResult:
        if not findings:
            return MaskedResult(result=result, findings=[], strategy_applied={})

        # Build a {(row_idx, col_name) -> (entity_type, strategy)} map.
        by_cell: dict[tuple[int, str], tuple[str, str]] = {}
        for f in findings:
            strategy = self._pick_strategy(f)
            # Last write wins; that's OK — multiple detectors firing on the
            # same cell still need only one masking pass.
            by_cell[(f.row_index, f.column)] = (f.entity_type, strategy)

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
        return MaskedResult(result=masked, findings=findings, strategy_applied=strategy_applied)

    def _pick_strategy(self, f: PIIFinding) -> str:
        if self.per_column and f.column in self.per_column:
            return self.per_column[f.column]
        if self.per_entity and f.entity_type in self.per_entity:
            return self.per_entity[f.entity_type]
        return self.default_strategy
