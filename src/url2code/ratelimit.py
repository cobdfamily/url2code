"""In-process token-bucket rate limiting for url2code.

Opt-in per endpoint via ``limits.rate_limit`` in the YAML.
The bucket is keyed by (endpoint name, client IP) and lives
in THIS process only. With multiple uvicorn workers each
worker keeps its own buckets, so N workers allow up to ~N x
the configured rate. For a hard, coordinated global limit,
rate-limit at the reverse proxy; this is a cheap in-app
backstop against a single hot client, not a distributed
limiter.

The limiter is intentionally dependency-free (no FastAPI /
Starlette import) so it unit-tests as plain Python; the
`client_ip` helper duck-types the request object (anything
with `.headers` and `.client`).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float  # monotonic timestamp of the last refill


class RateLimiter:
    """Token bucket per key.

    Each key gets `capacity` tokens, refilled continuously at
    `capacity / window_seconds` tokens per second up to the
    cap. A request consumes one token; when the bucket can't
    spare one the request is denied and told how many seconds
    until the next token refills.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(
        self,
        key: str,
        capacity: int,
        window_seconds: float,
        *,
        now: float | None = None,
    ) -> tuple[bool, float]:
        """Consume one token for `key`.

        Returns ``(allowed, retry_after_seconds)``. retry_after
        is ``0.0`` when allowed. `now` (monotonic seconds) is
        injectable so tests can advance time deterministically.
        """
        if capacity <= 0:
            # "Allow nothing." retry_after has no real meaning;
            # report the window so a caller still sends a sane
            # Retry-After rather than 0.
            return False, float(window_seconds)

        refill_rate = capacity / window_seconds  # tokens per second
        current = time.monotonic() if now is None else now

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                # First request for this key: start full, spend one.
                self._buckets[key] = _Bucket(tokens=capacity - 1.0, updated=current)
                return True, 0.0

            elapsed = max(0.0, current - bucket.updated)
            bucket.tokens = min(float(capacity), bucket.tokens + elapsed * refill_rate)
            bucket.updated = current

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0

            # Not enough for a whole token yet; time until one refills.
            deficit = 1.0 - bucket.tokens
            return False, deficit / refill_rate


def client_ip(request) -> str:
    """Best-effort client IP for rate-limit keying.

    Prefers the left-most ``X-Forwarded-For`` hop, since
    url2code runs behind a TLS-terminating reverse proxy that
    sets it; falls back to the socket peer, then ``"unknown"``.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    if client is not None and getattr(client, "host", None):
        return client.host
    return "unknown"
