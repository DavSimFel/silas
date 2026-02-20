"""Tests for UX quality metrics (§0.5.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from silas.models.ux_metrics import ApprovalEvent, BatchEvent, UXMetricsSummary
from silas.topics.ux_metrics import UXMetricsCollector

# -- helpers ----------------------------------------------------------------


def _fixed_now(offset_hours: float = 0.0) -> datetime:
    return datetime(2026, 1, 15, 12, 0, tzinfo=UTC) + timedelta(hours=offset_hours)


# -- basic recording & retrieval -------------------------------------------


def test_empty_metrics_returns_zeroes() -> None:
    collector = UXMetricsCollector()
    summary = collector.get_metrics_summary()

    assert summary.total_decisions == 0
    assert summary.median_decision_time_ms == 0.0
    assert summary.approval_rate == 0.0
    assert summary.decline_rate == 0.0
    assert summary.batch_taps_per_item == 0.0
    assert summary.decisions_per_hour == 0.0
    assert summary.fatigue_score == 0.0


def test_record_and_retrieve_approval_metrics() -> None:
    collector = UXMetricsCollector()
    collector.record_approval_decision("t1", "approved", 500)
    collector.record_approval_decision("t2", "declined", 1500)

    summary = collector.get_metrics_summary(window_hours=1)

    assert summary.total_decisions == 2
    assert summary.median_decision_time_ms == 1000.0
    assert summary.approval_rate == 0.5
    assert summary.decline_rate == 0.5


def test_record_batch_interaction() -> None:
    collector = UXMetricsCollector()
    collector.record_approval_decision("t1", "approved", 200)
    collector.record_batch_interaction("b1", taps=3, items=6)
    collector.record_batch_interaction("b2", taps=5, items=4)

    summary = collector.get_metrics_summary()

    # (3+5) / (6+4) = 0.8
    assert summary.batch_taps_per_item == pytest.approx(0.8)


def test_decisions_per_hour() -> None:
    collector = UXMetricsCollector()
    for i in range(10):
        collector.record_approval_decision(f"t{i}", "approved", 100)

    summary = collector.get_metrics_summary(window_hours=2)
    assert summary.decisions_per_hour == pytest.approx(5.0)


# -- fatigue score ---------------------------------------------------------


def test_fatigue_score_increases_with_slower_decisions() -> None:
    """When later decisions take much longer, fatigue should rise."""
    collector = UXMetricsCollector()

    # First batch: fast decisions
    for i in range(10):
        collector.record_approval_decision(f"fast-{i}", "approved", 200)
    # Second batch: much slower (simulating tired user)
    for i in range(10):
        collector.record_approval_decision(f"slow-{i}", "approved", 2000)

    summary = collector.get_metrics_summary()
    assert summary.fatigue_score > 0.5, "Fatigue should be high when decisions slow down"


def test_fatigue_score_zero_for_consistent_times() -> None:
    collector = UXMetricsCollector()
    for i in range(20):
        collector.record_approval_decision(f"t{i}", "approved", 500)

    summary = collector.get_metrics_summary()
    assert summary.fatigue_score == 0.0


def test_fatigue_score_zero_with_few_events() -> None:
    """Need ≥4 events to detect a trend."""
    collector = UXMetricsCollector()
    collector.record_approval_decision("t1", "approved", 100)
    collector.record_approval_decision("t2", "approved", 9000)

    summary = collector.get_metrics_summary()
    assert summary.fatigue_score == 0.0


# -- window filtering ------------------------------------------------------


def test_window_filtering_excludes_old_events() -> None:
    collector = UXMetricsCollector()

    old_time = _fixed_now(-48)
    recent_time = _fixed_now()

    # Inject an old event by patching datetime.now inside record
    with patch("silas.topics.ux_metrics.datetime") as mock_dt:
        mock_dt.now.return_value = old_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        collector.record_approval_decision("old", "declined", 5000)

    with patch("silas.topics.ux_metrics.datetime") as mock_dt:
        mock_dt.now.return_value = recent_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        collector.record_approval_decision("new", "approved", 300)

    with patch("silas.topics.ux_metrics.datetime") as mock_dt:
        mock_dt.now.return_value = recent_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        summary = collector.get_metrics_summary(window_hours=24)

    assert summary.total_decisions == 1
    assert summary.approval_rate == 1.0


# -- ring buffer eviction --------------------------------------------------


def test_ring_buffer_evicts_old_events() -> None:
    collector = UXMetricsCollector(max_events=5)

    for i in range(10):
        collector.record_approval_decision(f"t{i}", "approved", i * 100)

    # Only last 5 should remain
    assert len(collector._approval_events) == 5
    assert collector._approval_events[0].token_id == "t5"


def test_ring_buffer_evicts_batch_events() -> None:
    collector = UXMetricsCollector(max_events=3)

    for i in range(7):
        collector.record_batch_interaction(f"b{i}", taps=1, items=1)

    assert len(collector._batch_events) == 3
    assert collector._batch_events[0].batch_id == "b4"


# -- pydantic models -------------------------------------------------------


def test_ux_metrics_summary_fatigue_clamped() -> None:
    """fatigue_score must be in [0, 1]."""
    s = UXMetricsSummary(window_hours=24, fatigue_score=0.5)
    assert s.fatigue_score == 0.5

    with pytest.raises(ValidationError):
        UXMetricsSummary(window_hours=24, fatigue_score=1.5)

    with pytest.raises(ValidationError):
        UXMetricsSummary(window_hours=24, fatigue_score=-0.1)


def test_approval_event_model() -> None:
    e = ApprovalEvent(
        token_id="t1",
        decision="approved",
        duration_ms=500,
        recorded_at=datetime.now(UTC),
    )
    assert e.token_id == "t1"


def test_batch_event_model() -> None:
    e = BatchEvent(
        batch_id="b1",
        taps=3,
        items=5,
        recorded_at=datetime.now(UTC),
    )
    assert e.items == 5


# -- manager integration ---------------------------------------------------


def test_manager_records_ux_metrics_on_resolve() -> None:
    """LiveApprovalManager should feed the collector on resolve()."""
    from silas.gates.approval_manager import LiveApprovalManager
    from silas.models.approval import ApprovalScope, ApprovalVerdict
    from silas.models.work import WorkItem

    collector = UXMetricsCollector()
    mgr = LiveApprovalManager(ux_metrics=collector)

    # Create a minimal work item — only need .id and .plan_hash()
    wi = WorkItem(id="wi-1", type="task", title="test", body="test")
    token = mgr.request_approval(wi, ApprovalScope.full_plan)

    mgr.resolve(token.token_id, ApprovalVerdict.approved, "user")

    summary = collector.get_metrics_summary(window_hours=1)
    assert summary.total_decisions == 1
    assert summary.approval_rate == 1.0
    assert summary.median_decision_time_ms >= 0
