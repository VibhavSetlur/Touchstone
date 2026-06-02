# Adding a PII detector

Custom PII detectors are tiny — usually 5-20 lines. They plug into the
existing detector pipeline and benefit from all the masking strategies for
free.

## The interface

```python
@dataclass(slots=True)
class Detector(ABC):
    entity_type: ClassVar[str] = "UNKNOWN"

    @abstractmethod
    def confidence(self, value: str) -> float:
        """Return [0.0, 1.0] confidence that the value contains an entity
        of `entity_type`."""

    def confidence_column(self, column_name: str) -> float:
        """Override for column-name heuristics that flag every value in a
        matched column."""
        return 0.0
```

## A regex-based detector

```python
import re
from touchstone.security.pii import Detector, _RegexDetector, register

@register("internal_employee_id")
class EmployeeIDDetector(_RegexDetector):
    entity_type = "EMPLOYEE_ID"
    pattern = re.compile(r"\bEMP-\d{7}\b")
```

That's it. Enable it by listing it in the config:

```toml
[security]
pii_detectors_enabled = ["column_name", "regex", "internal_employee_id"]
```

## A detector with custom validation

Touchstone's `CreditCard` detector is a good template — regex-match plus a
checksum (Luhn) to drop false positives:

```python
@register("my_account_number")
class MyAccountNumber(Detector):
    entity_type = "ACCOUNT_NUMBER"
    PATTERN = re.compile(r"\b\d{10,12}\b")

    def confidence(self, value: str) -> float:
        if not isinstance(value, str):
            return 0.0
        for m in self.PATTERN.findall(value):
            if _checksum_valid(m):
                return 0.95
        return 0.0


def _checksum_valid(s: str) -> bool:
    ...  # your validation
```

## A column-name detector

```python
@register("ssn_column")
class SSNColumnDetector(Detector):
    entity_type = "US_SSN"
    PATTERN = re.compile(r"\bssn|social_security", re.I)

    def confidence(self, value: str) -> float:
        return 0.0   # we flag by name, not value

    def confidence_column(self, column_name: str) -> float:
        return 0.9 if self.PATTERN.search(column_name) else 0.0
```

## A detector that wraps an ML model

For ML-based detection (NER, classification), wrap the model in a Detector:

```python
@register("ml_pii")
class MLPIIDetector(Detector):
    entity_type = "GENERIC_PII"

    def __init__(self):
        # Lazy: import + load only when constructed.
        from my_pii_model import Classifier
        self._clf = Classifier.from_pretrained("acme/pii-v3")

    def confidence(self, value: str) -> float:
        if not isinstance(value, str) or len(value) > 2048:
            return 0.0
        return float(self._clf.predict_proba(value)[1])  # P(is_pii)
```

If your model is slow, document the throughput cost. Operators may want to
enable it only for high-stakes connections.

## Testing your detector

```python
# tests/unit/test_my_detector.py
from touchstone.security.pii import PIIDetector
from touchstone.types import Column, Engine, QueryResult


def test_my_detector_flags_emp_ids():
    detector = PIIDetector(threshold=0.4, enabled=["internal_employee_id"])
    result = QueryResult(
        columns=[Column(name="note", type="text")],
        rows=[("ping EMP-1234567 today",), ("nothing to see",)],
        row_count=2, engine=Engine.DUCKDB,
    )
    findings = detector.scan(result)
    assert any(f.row_index == 0 for f in findings)
    assert not any(f.row_index == 1 for f in findings)
```

## What gets shipped with Touchstone

We ship: EMAIL, US_SSN, US/E.164 phone, IPv4, AWS access key, GitHub PAT,
Stripe key, IBAN, credit card (Luhn-validated), and column-name heuristics
for common shapes. Country-specific national-IDs (UK NHS, German Personal-
ausweis, India PAN, etc.) live in community detector packs — pip-install
them and they auto-register.
