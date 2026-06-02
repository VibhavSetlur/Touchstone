"""PII detection pipeline.

Three layers, each optional, run in order:
  1. Column-name heuristic — fast, no false negatives on well-named columns.
  2. Regex bank — SSN, credit card (Luhn-validated), IBAN, phone, IP, API keys.
  3. Presidio NER — PERSON, LOCATION, ORG, MEDICAL_LICENSE, etc. Slow; opt-in.

Custom detectors are 20 lines:

    @register("internal_employee_id")
    class EmpID(Detector):
        pattern = re.compile(r"\\bEMP-\\d{7}\\b")
        def confidence(self, value): return 0.95 if self.pattern.search(value) else 0.0
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from touchstone.types import PIIFinding, QueryResult


@dataclass(slots=True)
class Detector(ABC):
    """Base for a PII detector. Subclasses set `entity_type` and implement
    `confidence(value)` (or `confidence_column(column_name)` for column-only
    detectors)."""

    entity_type: ClassVar[str] = "UNKNOWN"

    @abstractmethod
    def confidence(self, value: str) -> float:
        """Return a confidence in [0.0, 1.0] that the value contains an entity
        of `entity_type`."""

    def confidence_column(self, column_name: str) -> float:
        """Override to flag columns by name regardless of value."""
        return 0.0


_REGISTRY: dict[str, type[Detector]] = {}


def register(name: str):
    def deco(cls: type[Detector]) -> type[Detector]:
        _REGISTRY[name] = cls
        return cls
    return deco


# -- Column-name heuristic --------------------------------------------------

@register("column_name")
class ColumnNameDetector(Detector):
    entity_type = "BY_COLUMN_NAME"
    PATTERNS: ClassVar[dict[str, str]] = {
        r"\bemail(_address)?\b":    "EMAIL",
        r"\bphone(_number)?\b":     "PHONE",
        r"\bssn\b|\bsocial_security": "US_SSN",
        r"\b(date_of_birth|dob|birthdate|birth_date)\b": "DOB",
        r"\b(first|last|full|given|family|middle)_name\b": "PERSON",
        r"\b(street|address|addr|city|zip|postcode|postal_code)\b": "ADDRESS",
        r"\b(passport|driver_license|drivers_license|license_number)\b": "ID_DOCUMENT",
        r"\b(credit_card|cc_number|card_number)\b": "CREDIT_CARD",
        r"\b(api_key|access_token|secret|password|pwd|api_secret)\b": "CREDENTIAL",
        r"\b(ip_address|ipv4|ipv6)\b": "IP",
    }
    _COMPILED: ClassVar[list[tuple[re.Pattern, str]]] = [
        (re.compile(p, re.I), t) for p, t in PATTERNS.items()
    ]

    def confidence(self, value: str) -> float:
        return 0.0

    def confidence_column(self, column_name: str) -> float:
        return 0.85 if any(p.search(column_name) for p, _ in self._COMPILED) else 0.0

    def matched_entity(self, column_name: str) -> str:
        for p, t in self._COMPILED:
            if p.search(column_name):
                return t
        return "BY_COLUMN_NAME"


# -- Regex bank -------------------------------------------------------------

class _RegexDetector(Detector):
    pattern: ClassVar[re.Pattern]
    def confidence(self, value: str) -> float:
        if not isinstance(value, str):
            return 0.0
        return 0.95 if self.pattern.search(value) else 0.0


@register("us_ssn")
class USSSNDetector(_RegexDetector):
    entity_type = "US_SSN"
    pattern = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


@register("email")
class EmailDetector(_RegexDetector):
    entity_type = "EMAIL"
    pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


@register("phone_e164")
class PhoneE164Detector(_RegexDetector):
    entity_type = "PHONE"
    pattern = re.compile(r"\+\d{8,15}\b")


@register("phone_us")
class PhoneUSDetector(_RegexDetector):
    entity_type = "PHONE"
    pattern = re.compile(r"\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


@register("ipv4")
class IPv4Detector(_RegexDetector):
    entity_type = "IP"
    pattern = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")


@register("aws_access_key")
class AWSAccessKey(_RegexDetector):
    entity_type = "CREDENTIAL"
    pattern = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


@register("github_pat")
class GitHubPAT(_RegexDetector):
    entity_type = "CREDENTIAL"
    pattern = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")


@register("stripe_key")
class StripeKey(_RegexDetector):
    entity_type = "CREDENTIAL"
    pattern = re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b")


@register("iban")
class IBAN(_RegexDetector):
    entity_type = "IBAN"
    pattern = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")


@register("credit_card")
class CreditCard(Detector):
    """Detects credit-card-looking numbers and validates via Luhn — Luhn rules
    out random 16-digit strings, which dramatically reduces false positives."""

    entity_type = "CREDIT_CARD"
    PATTERN: ClassVar[re.Pattern] = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

    def confidence(self, value: str) -> float:
        if not isinstance(value, str):
            return 0.0
        for m in self.PATTERN.findall(value):
            digits = re.sub(r"\D", "", m)
            if 13 <= len(digits) <= 19 and _luhn(digits):
                return 0.95
        return 0.0


def _luhn(digits: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# -- Detector orchestrator --------------------------------------------------

@dataclass(slots=True)
class PIIDetector:
    threshold: float = 0.4
    enabled: list[str] = field(default_factory=lambda: ["column_name", "regex"])
    # `regex` is shorthand — expands to all regex-based detectors.

    def __post_init__(self) -> None:
        names: list[str] = []
        for n in self.enabled:
            if n == "regex":
                names.extend(k for k, v in _REGISTRY.items()
                             if issubclass(v, _RegexDetector) or v is CreditCard)
            elif n == "presidio":
                # Lazy: only import presidio when first used.
                names.append("presidio")
            else:
                names.append(n)
        self._detectors: list[Detector] = []
        self._column_detector: ColumnNameDetector | None = None
        for name in names:
            if name == "presidio":
                self._detectors.append(_PresidioDetector())
            elif name in _REGISTRY:
                d = _REGISTRY[name]()
                if isinstance(d, ColumnNameDetector):
                    self._column_detector = d
                else:
                    self._detectors.append(d)

    def scan(self, result: QueryResult) -> list[PIIFinding]:
        findings: list[PIIFinding] = []

        # First pass: column-name heuristic — flags every value in matched columns.
        if self._column_detector is not None:
            for col_idx, col in enumerate(result.columns):
                conf = self._column_detector.confidence_column(col.name)
                if conf >= self.threshold:
                    entity = self._column_detector.matched_entity(col.name)
                    for row_idx in range(len(result.rows)):
                        findings.append(PIIFinding(
                            column=col.name, row_index=row_idx,
                            detector="column_name", entity_type=entity, confidence=conf,
                        ))

        # Second pass: value-based detectors.
        for row_idx, row in enumerate(result.rows):
            for col_idx, value in enumerate(row):
                if value is None:
                    continue
                s = value if isinstance(value, str) else str(value)
                for det in self._detectors:
                    conf = det.confidence(s)
                    if conf >= self.threshold:
                        findings.append(PIIFinding(
                            column=result.columns[col_idx].name,
                            row_index=row_idx,
                            detector=type(det).__name__,
                            entity_type=det.entity_type,
                            confidence=conf,
                        ))

        return findings


class _PresidioDetector(Detector):
    """Wraps Microsoft Presidio. Loads spaCy model lazily."""

    entity_type = "PRESIDIO"

    def __init__(self) -> None:
        from presidio_analyzer import AnalyzerEngine  # type: ignore[import-untyped]
        self._analyzer = AnalyzerEngine()

    def confidence(self, value: str) -> float:
        if not isinstance(value, str):
            return 0.0
        try:
            results = self._analyzer.analyze(text=value, language="en")
        except Exception:
            return 0.0
        if not results:
            return 0.0
        # Return the max confidence; the entity type is the top one.
        top = max(results, key=lambda r: r.score)
        self.entity_type = top.entity_type
        return float(top.score)
