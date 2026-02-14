"""Tests for silas.core.metrics — Prometheus metrics and no-op stubs."""

from __future__ import annotations

import time

from silas.core.metrics import (
    ACTIVE_WEBSOCKETS,
    LLM_CALLS_TOTAL,
    LLM_TOKENS_TOTAL,
    QUEUE_LEASE_TIMEOUTS_TOTAL,
    QUEUE_MESSAGES_TOTAL,
    TURN_DURATION_SECONDS,
    TURNS_TOTAL,
    _NoOpMetric,
    metrics_generate_latest,
    observe_turn_duration,
)


class TestNoOpMetric:
    def test_labels_returns_self(self) -> None:
        m = _NoOpMetric()
        assert m.labels(agent="x") is m

    def test_chained_labels_and_inc(self) -> None:
        m = _NoOpMetric()
        m.labels(a="1").labels(b="2").inc()  # should not raise

    def test_inc_default(self) -> None:
        _NoOpMetric().inc()  # no-op, no raise

    def test_inc_custom(self) -> None:
        _NoOpMetric().inc(5)

    def test_dec_default(self) -> None:
        _NoOpMetric().dec()

    def test_dec_custom(self) -> None:
        _NoOpMetric().dec(3)

    def test_observe(self) -> None:
        _NoOpMetric().observe(1.5)


class TestObserveTurnDuration:
    def test_context_manager_completes(self) -> None:
        with observe_turn_duration("test-agent"):
            pass  # should not raise

    def test_context_manager_records_duration(self) -> None:
        start = time.monotonic()
        with observe_turn_duration("timer-agent"):
            time.sleep(0.01)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.01

    def test_context_manager_on_exception(self) -> None:
        """Duration is still observed even if body raises."""
        try:
            with observe_turn_duration("fail-agent"):
                raise ValueError("boom")
        except ValueError:
            pass  # expected


class TestMetricSingletons:
    """Verify that module-level metric singletons exist and are usable."""

    def test_turns_total(self) -> None:
        TURNS_TOTAL.labels(agent="test").inc()

    def test_turn_duration(self) -> None:
        TURN_DURATION_SECONDS.labels(agent="test").observe(0.5)

    def test_llm_calls(self) -> None:
        LLM_CALLS_TOTAL.labels(model="gpt-4").inc()

    def test_llm_tokens(self) -> None:
        LLM_TOKENS_TOTAL.labels(model="gpt-4", direction="input").inc(100)

    def test_queue_messages(self) -> None:
        QUEUE_MESSAGES_TOTAL.labels(queue_name="main", message_kind="task").inc()

    def test_queue_lease_timeouts(self) -> None:
        QUEUE_LEASE_TIMEOUTS_TOTAL.inc()

    def test_active_websockets(self) -> None:
        ACTIVE_WEBSOCKETS.inc()
        ACTIVE_WEBSOCKETS.dec()

    def test_generate_latest(self) -> None:
        output = metrics_generate_latest()
        assert isinstance(output, bytes)
