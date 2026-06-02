"""Test-case generation.

Strategy: profile-derived heuristics first (deterministic, no LLM needed),
optionally enriched with an LLM call when the profile alone is ambiguous.

Returns expectations in the same YAML shape that `validator.check_data_quality`
consumes — so the AI assistant can immediately try them and the human can
diff them into the repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from touchstone.qa.profiler import TableProfile


@dataclass(slots=True)
class GeneratedExpectations:
    table: str
    expectations: list[dict[str, Any]] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)

    def to_yaml(self) -> str:
        import yaml
        return yaml.safe_dump({
            "table": self.table,
            "expectations": self.expectations,
        }, sort_keys=False)


def generate_test_cases(
    profile: TableProfile,
    *,
    include_llm_suggestions: bool = False,
) -> GeneratedExpectations:
    """Generate expectations from a table profile."""
    gen = GeneratedExpectations(table=profile.table.qualified())

    # Row count: order-of-magnitude bounds with 10x headroom.
    if profile.row_count > 0:
        lo = max(1, profile.row_count // 10)
        hi = profile.row_count * 10
        gen.expectations.append({"row_count_between": {"min": lo, "max": hi}})
        gen.rationale.append(
            f"row_count_between derived from observed {profile.row_count:,} rows ±10x"
        )

    for col in profile.columns:
        # Strongly-typed id columns → not null + unique.
        if col.semantic_type == "identifier":
            gen.expectations.append({"column_not_null": col.name})
            if col.distinct_ratio is not None and col.distinct_ratio > 0.999:
                gen.expectations.append({"column_unique": col.name})
                gen.rationale.append(f"{col.name}: distinct_ratio {col.distinct_ratio:.4f} → unique")

        # Low-cardinality category → enum check.
        if col.distinct_count is not None and 1 <= col.distinct_count <= 12 and col.top_values:
            gen.expectations.append({
                "column_values_in_set": {
                    "column": col.name,
                    "set": [v for v, _ in col.top_values],
                },
            })
            gen.rationale.append(
                f"{col.name}: low cardinality ({col.distinct_count}) → enum check from top values"
            )

        # Timestamp columns → freshness (default 1h).
        if col.semantic_type == "timestamp":
            gen.expectations.append({
                "column_freshness_seconds": {"column": col.name, "max": 3600},
            })

        # Never-null observation → assert not null.
        if col.null_rate == 0 and col.semantic_type != "identifier":
            gen.expectations.append({"column_not_null": col.name})
            gen.rationale.append(f"{col.name}: observed null_rate 0 → assert not null")

    if include_llm_suggestions:
        # LLM-derived suggestions are appended via touchstone.llm.adapter (roadmap).
        gen.rationale.append("LLM suggestions skipped — adapter not yet wired in OSS build.")

    return gen
