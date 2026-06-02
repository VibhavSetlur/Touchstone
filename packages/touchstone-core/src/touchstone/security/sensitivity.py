"""Operator-declared column sensitivity.

PII detectors (regex / Presidio) are great at standard cases but blind to
columns where the danger lives in the *meaning* of the column, not the
shape of its values. Examples:

  - `user_notes` containing raw medical histories.
  - `support_ticket_body` containing arbitrary PII pasted by customers.
  - `bank_memo` containing routing numbers, names, free-form context.
  - any LOB / VARCHAR(MAX) / TEXT column populated by humans.

For those cases, regex/NER will MISS most of the leakage. The fix is to
let operators DECLARE which columns are sensitive, regardless of value
shape. Declaration formats:

  1. Per-connection tags in `touchstone.toml`:

         [sensitivity.warehouse-ro]
         redact = ["users.notes", "support.tickets.body"]
         hash   = ["users.email", "users.ssn"]
         tokenize = ["orders.customer_id"]
         never_leaves = ["medical.records.*"]    # returned as type only

  2. An external sensitivity catalog loaded at startup (CSV / YAML).
  3. Per-column comments in the DB (Postgres `COMMENT ON COLUMN x IS
     'sensitivity:redact'`) — read by the connector on `describe_table`
     and merged into the runtime policy.

`never_leaves` is the strongest tier: the column is replaced with a
type-only descriptor like `<TEXT len=437>` so the LLM knows the column
exists and has data but never sees the bytes.

Additionally, this module configures the "free-text mode" detector — any
text column over N characters automatically gets the most aggressive
masking strategy unless explicitly exempt.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any


SENSITIVITY_TIERS = ("never_leaves", "redact", "hash", "tokenize", "partial", "synthetic", "allow")


@dataclass(slots=True)
class SensitivityCatalog:
    """Maps qualified column names → sensitivity tier.

    Keys are glob patterns like `users.email`, `medical.records.*`,
    `*.notes`. Lookups walk most-specific to least-specific.
    """

    rules: dict[str, str] = field(default_factory=dict)
    free_text_threshold_chars: int = 200
    free_text_default_tier: str = "redact"
    exempt_free_text: list[str] = field(default_factory=list)  # globs

    def tier_for(self, qualified_column: str, column_type: str = "",
                 sample_value: Any = None) -> str:
        """Return the sensitivity tier for a column. Most-specific rule
        wins. Free-text columns (TEXT / VARCHAR(MAX) / CLOB / large average
        length) default to `free_text_default_tier` unless exempt."""
        # 1. Explicit rules — most-specific glob first.
        matches = [(pat, tier) for pat, tier in self.rules.items()
                   if fnmatch.fnmatchcase(qualified_column, pat)]
        if matches:
            matches.sort(key=lambda kv: -len(kv[0].replace("*", "")))
            return matches[0][1]
        # 2. Free-text heuristic.
        if self._is_free_text(column_type, sample_value):
            for ex in self.exempt_free_text:
                if fnmatch.fnmatchcase(qualified_column, ex):
                    return "allow"
            return self.free_text_default_tier
        return "allow"

    def _is_free_text(self, col_type: str, sample: Any) -> bool:
        ct = (col_type or "").lower()
        if "text" in ct or "clob" in ct or "blob" in ct:
            return True
        if "varchar" in ct:
            # Heuristic: VARCHAR(>=255) treated as free-text; smaller as category.
            try:
                n = int(ct.split("(", 1)[1].rstrip(")"))
                return n >= 255
            except (IndexError, ValueError):
                return True
        if isinstance(sample, str) and len(sample) > self.free_text_threshold_chars:
            return True
        return False

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> SensitivityCatalog:
        rules: dict[str, str] = {}
        for tier in SENSITIVITY_TIERS:
            cols = raw.get(tier) or []
            for c in cols:
                rules[c] = tier
        return cls(
            rules=rules,
            free_text_threshold_chars=raw.get("free_text_threshold_chars", 200),
            free_text_default_tier=raw.get("free_text_default_tier", "redact"),
            exempt_free_text=raw.get("exempt_free_text", []) or [],
        )


@dataclass(slots=True)
class SensitivityRegistry:
    """Per-connection sensitivity catalogs."""

    by_connection: dict[str, SensitivityCatalog] = field(default_factory=dict)
    default: SensitivityCatalog = field(default_factory=SensitivityCatalog)

    def for_connection(self, name: str) -> SensitivityCatalog:
        return self.by_connection.get(name, self.default)

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> SensitivityRegistry:
        by_conn = {conn: SensitivityCatalog.from_config(spec)
                   for conn, spec in raw.items() if isinstance(spec, dict)}
        default = SensitivityCatalog()
        return cls(by_connection=by_conn, default=default)
