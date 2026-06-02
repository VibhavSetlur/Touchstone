"""Per-assistant token-bucket rate limiter.

In-process (per Touchstone instance). For multi-process deployments behind a
gateway, swap in a Redis-backed implementation — the interface is one method.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(slots=True)
class _Bucket:
    tokens: float
    last_refill: float


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.refill_per_second = per_minute / 60.0
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.per_minute), last_refill=now)
                self._buckets[key] = bucket
            elapsed = now - bucket.last_refill
            bucket.tokens = min(
                float(self.per_minute), bucket.tokens + elapsed * self.refill_per_second
            )
            bucket.last_refill = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False
