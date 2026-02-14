from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from silas.models.draft import DraftReview, DraftVerdict
from silas.models.undo import UndoEntry
from silas.proactivity.fatigue import ApprovalFatigueTracker
from silas.proactivity.ux_metrics import UXMetricsCollector


def _utc_now() -> datetime:
    return datetime.now(UTC)


def test_draft_review_creation() -> None:
    review = DraftReview(review_id="r1", context="email reply", draft="Thanks for the update.")
    assert review.verdict is None
    assert review.decided_at is None
    assert review.created_at.tzinfo is not None


def test_draft_verdict_enum_values() -> None:
    assert {verdict.value for verdict in DraftVerdict} == {
        "approve",
        "edit",
        "rephrase",
        "reject",
    }


def test_undo_entry_field_validation() -> None:
    now = _utc_now()
    with pytest.raises(ValidationError):
        UndoEntry(
            entry_id="u1",
            scope_id="owner",
            execution_id="e1",
            reverse_actions=[{"op": "revert"}],
            created_at=datetime(2026, 1, 1, 0, 0, 0),
            expires_at=now + timedelta(minutes=1),
        )

    with pytest.raises(ValidationError):
        UndoEntry(
            entry_id="u2",
            scope_id="owner",
            execution_id="e2",
            reverse_actions=[{"op": "revert"}],
            created_at=now,
            expires_at=now,
        )

    with pytest.raises(ValidationError):
        UndoEntry(
            entry_id="u3",
            scope_id="owner",
            execution_id="e3",
            reverse_actions=[{"op": "revert"}],
            created_at=now,
            expires_at=now + timedelta(minutes=1),
            undone_at=now - timedelta(seconds=1),
        )


def test_approval_fatigue_tracker_cadence_tracking() -> None:
    tracker = ApprovalFatigueTracker(rolling_window_size=10)
    start = _utc_now()

    tracker.record_decision(
        "owner",
        "medium",
        requested_at=start,
        decided_at=start + timedelta(seconds=2),
    )
    tracker.record_decision(
        "owner",
        "high",
        requested_at=start + timedelta(seconds=10),
        decided_at=start + timedelta(seconds=13),
    )
    tracker.record_decision(
        "owner",
        "irreversible",
        requested_at=start + timedelta(seconds=20),
        decided_at=start + timedelta(seconds=24),
    )

    status = tracker.status("owner")
    assert status["median_decision_time_seconds"] == pytest.approx(3.0)
    assert status["cadence_decisions_per_minute"] > 0.0


def test_approval_fatigue_tracker_density_cues_and_no_throttle() -> None:
    tracker = ApprovalFatigueTracker(queue_density_threshold=5)

    assert tracker.record_queue_depth("owner", 5) is False
    assert tracker.record_queue_depth("owner", 6) is True
    assert tracker.fatigue_trigger_count("owner") == 1

    assert tracker.record_queue_depth("owner", 7) is True
    assert tracker.fatigue_trigger_count("owner") == 1

    assert tracker.record_queue_depth("owner", 2) is False
    assert tracker.record_queue_depth("owner", 8) is True
    assert tracker.fatigue_trigger_count("owner") == 2

    assert tracker.should_throttle("owner") is False


def test_ux_metrics_collector_tracks_all_metric_types() -> None:
    collector = UXMetricsCollector(rolling_window_size=10)
    start = _utc_now()

    collector.record_decision("owner", start, start + timedelta(seconds=2))
    collector.record_decision(
        "owner",
        start + timedelta(seconds=10),
        start + timedelta(seconds=14),
        verdict="declined",
    )
    collector.record_batch("owner", taps=2)
    collector.record_batch("owner", taps=4)
    collector.record_correction("owner", outcome="edit_selection")
    collector.record_undo("owner")
    collector.record_fatigue_trigger("owner")
    collector.record_fatigue_trigger("owner")
    collector.record_free_text("owner", used_free_text=True)
    collector.record_free_text("owner", used_free_text=False)
    collector.record_free_text("owner", used_free_text=True)

    metrics = collector.get_metrics("owner")
    assert metrics["decision_time"] == pytest.approx(3.0)
    assert metrics["taps_per_batch"] == pytest.approx(3.0)
    assert metrics["decline_rate"] == pytest.approx(0.25)
    assert metrics["correction_rate"] == pytest.approx(0.75)
    assert metrics["undo_rate"] == pytest.approx(0.25)
    assert metrics["approval_fatigue_triggers"] == 2
    assert metrics["free_text_usage_rate"] == pytest.approx(2 / 3)


def test_ux_metrics_collector_respects_rolling_windows() -> None:
    collector = UXMetricsCollector(rolling_window_size=3)
    start = _utc_now()

    collector.record_decision("owner", start, start + timedelta(seconds=1))
    collector.record_decision(
        "owner",
        start + timedelta(seconds=10),
        start + timedelta(seconds=12),
    )
    collector.record_decision(
        "owner",
        start + timedelta(seconds=20),
        start + timedelta(seconds=29),
    )
    collector.record_decision(
        "owner",
        start + timedelta(seconds=30),
        start + timedelta(seconds=34),
    )

    collector.record_batch("owner", taps=1)
    collector.record_batch("owner", taps=2)
    collector.record_batch("owner", taps=3)
    collector.record_batch("owner", taps=9)

    collector.record_correction("owner", outcome="declined")
    collector.record_undo("owner")

    collector.record_free_text("owner", used_free_text=True)
    collector.record_free_text("owner", used_free_text=False)
    collector.record_free_text("owner", used_free_text=True)
    collector.record_free_text("owner", used_free_text=False)

    metrics = collector.get_metrics("owner")
    assert metrics["decision_time"] == pytest.approx(4.0)
    assert metrics["taps_per_batch"] == pytest.approx((2 + 3 + 9) / 3)
    assert metrics["decline_rate"] == pytest.approx(1 / 3)
    assert metrics["undo_rate"] == pytest.approx(1 / 3)
    assert metrics["correction_rate"] == pytest.approx(2 / 3)
    assert metrics["free_text_usage_rate"] == pytest.approx(1 / 3)


def test_ux_metrics_collector_rejects_naive_decision_timestamps() -> None:
    collector = UXMetricsCollector()
    aware_now = _utc_now()
    naive_now = datetime(2026, 1, 1, 12, 0, 0)
    with pytest.raises(ValueError):
        collector.record_decision("owner", naive_now, aware_now)
