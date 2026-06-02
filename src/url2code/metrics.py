"""Dependency-free Prometheus metrics for url2code.

Hand-rolled text exposition (no `prometheus-client`) to keep
the engine's runtime dependency surface minimal — the same
stdlib-lean stance as the rest of the engine. Counters/gauges/
histogram are kept in process under a lock.

Multi-worker caveat (same as the rate limiter): with multiple
uvicorn workers each worker exposes its own series. Scrape each
worker, or aggregate at the collector; the numbers are
per-process, not cluster-wide.

Exposed series:
  - url2code_requests_total{endpoint,status}       counter
  - url2code_in_flight_requests{endpoint}          gauge
  - url2code_request_duration_seconds{endpoint}    histogram
    (CLI wall time; only observed when the command actually ran)
"""

from __future__ import annotations

import threading

# Histogram buckets in seconds, tuned for CLI-wrapper latencies:
# tens of milliseconds up to the 30s default command timeout.
_BUCKETS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


def _format_float(value: float) -> str:
    # Prometheus wants plain decimals; trim a trailing ".0" so
    # bucket labels read le="0.05" / le="30" rather than "30.0".
    text = repr(float(value))
    return text[:-2] if text.endswith(".0") else text


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str], int] = {}
        self._inflight: dict[str, int] = {}
        # Per-endpoint cumulative bucket counts (index i == "<= _BUCKETS[i]"),
        # plus running sum and total count for the histogram.
        self._buckets: dict[str, list[int]] = {}
        self._sum: dict[str, float] = {}
        self._count: dict[str, int] = {}

    def inc_request(self, endpoint: str, status: int | str) -> None:
        key = (endpoint, str(status))
        with self._lock:
            self._requests[key] = self._requests.get(key, 0) + 1

    def inc_inflight(self, endpoint: str) -> None:
        with self._lock:
            self._inflight[endpoint] = self._inflight.get(endpoint, 0) + 1

    def dec_inflight(self, endpoint: str) -> None:
        with self._lock:
            self._inflight[endpoint] = self._inflight.get(endpoint, 0) - 1

    def observe_duration(self, endpoint: str, seconds: float) -> None:
        with self._lock:
            counts = self._buckets.setdefault(endpoint, [0] * len(_BUCKETS))
            # Each observation lands in every bucket whose upper
            # bound it's <=, which is exactly Prometheus's
            # cumulative `le` bucket semantics.
            for i, bound in enumerate(_BUCKETS):
                if seconds <= bound:
                    counts[i] += 1
            self._sum[endpoint] = self._sum.get(endpoint, 0.0) + seconds
            self._count[endpoint] = self._count.get(endpoint, 0) + 1

    def render(self) -> str:
        """Render the current state as Prometheus text exposition."""
        lines: list[str] = []
        with self._lock:
            lines.append("# HELP url2code_requests_total Total requests by endpoint and status.")
            lines.append("# TYPE url2code_requests_total counter")
            for (endpoint, status), value in sorted(self._requests.items()):
                lines.append(
                    f'url2code_requests_total{{endpoint="{endpoint}",status="{status}"}} {value}'
                )

            lines.append("# HELP url2code_in_flight_requests In-flight requests by endpoint.")
            lines.append("# TYPE url2code_in_flight_requests gauge")
            for endpoint, value in sorted(self._inflight.items()):
                lines.append(f'url2code_in_flight_requests{{endpoint="{endpoint}"}} {value}')

            lines.append(
                "# HELP url2code_request_duration_seconds CLI run wall time by endpoint."
            )
            lines.append("# TYPE url2code_request_duration_seconds histogram")
            for endpoint in sorted(self._count):
                counts = self._buckets[endpoint]
                for i, bound in enumerate(_BUCKETS):
                    le = _format_float(bound)
                    lines.append(
                        f'url2code_request_duration_seconds_bucket'
                        f'{{endpoint="{endpoint}",le="{le}"}} {counts[i]}'
                    )
                total = self._count[endpoint]
                lines.append(
                    f'url2code_request_duration_seconds_bucket'
                    f'{{endpoint="{endpoint}",le="+Inf"}} {total}'
                )
                lines.append(
                    f'url2code_request_duration_seconds_sum'
                    f'{{endpoint="{endpoint}"}} {self._sum[endpoint]}'
                )
                lines.append(
                    f'url2code_request_duration_seconds_count'
                    f'{{endpoint="{endpoint}"}} {total}'
                )

        return "\n".join(lines) + "\n"
