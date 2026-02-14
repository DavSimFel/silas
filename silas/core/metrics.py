"""Prometheus metrics for the Silas runtime.

All metric objects are module-level singletons.  When ``prometheus_client`` is
not installed, every metric degrades to a no-op stub so the rest of the
codebase never needs to guard imports.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

try:
    from prometheus_client import Counter, Gauge, Histogram, generate_latest

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False


class _NoOpMetric:
    """Drop-in stub that accepts any call and does nothing."""

    def labels(self, *args: object, **kwargs: object) -> _NoOpMetric:
        return self

    def inc(self, amount: float = 1) -> None:
        pass

    def dec(self, amount: float = 1) -> None:
        pass

    def observe(self, amount: float) -> None:
        pass


def _noop_generate_latest() -> bytes:
    return b""


if _AVAILABLE:
    TURNS_TOTAL = Counter("silas_turns_total", "Total turn invocations", ["agent"])
    TURN_DURATION_SECONDS = Histogram(
        "silas_turn_duration_seconds", "Turn processing duration in seconds", ["agent"]
    )
    LLM_CALLS_TOTAL = Counter("silas_llm_calls_total", "Total LLM API calls", ["model"])
    LLM_TOKENS_TOTAL = Counter("silas_llm_tokens_total", "Total LLM tokens", ["model", "direction"])
    QUEUE_MESSAGES_TOTAL = Counter(
        "silas_queue_messages_total",
        "Total queue messages processed",
        ["queue_name", "message_kind"],
    )
    QUEUE_LEASE_TIMEOUTS_TOTAL = Counter(
        "silas_queue_lease_timeouts_total", "Total queue lease timeouts"
    )
    ACTIVE_WEBSOCKETS = Gauge("silas_active_websockets", "Currently active WebSocket connections")
    metrics_generate_latest = generate_latest
else:
    TURNS_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    TURN_DURATION_SECONDS = _NoOpMetric()  # type: ignore[assignment]
    LLM_CALLS_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    LLM_TOKENS_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    QUEUE_MESSAGES_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    QUEUE_LEASE_TIMEOUTS_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    ACTIVE_WEBSOCKETS = _NoOpMetric()  # type: ignore[assignment]
    metrics_generate_latest = _noop_generate_latest  # type: ignore[assignment]


@contextmanager
def observe_turn_duration(agent: str) -> Iterator[None]:
    """Context manager that increments turn counter and observes duration."""
    TURNS_TOTAL.labels(agent=agent).inc()
    start = time.monotonic()
    try:
        yield
    finally:
        TURN_DURATION_SECONDS.labels(agent=agent).observe(time.monotonic() - start)


__all__ = [
    "ACTIVE_WEBSOCKETS",
    "LLM_CALLS_TOTAL",
    "LLM_TOKENS_TOTAL",
    "QUEUE_LEASE_TIMEOUTS_TOTAL",
    "QUEUE_MESSAGES_TOTAL",
    "TURNS_TOTAL",
    "TURN_DURATION_SECONDS",
    "metrics_generate_latest",
    "observe_turn_duration",
]
