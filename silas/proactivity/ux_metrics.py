"""UX quality metrics collector (§0.5.6).

Tracks approval decision timing, batch interaction cost, and fatigue
signals using in-memory ring buffers — no persistence layer needed.

Two APIs coexist:
- Legacy scope-based API (record_decision, record_batch, get_metrics, etc.)
- New event-based API (record_approval_decision, record_batch_interaction, get_metrics_summary)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import mean, median

from silas.models.ux_metrics import ApprovalEvent, BatchEvent, UXMetricsSummary

_DECISION_VERDICTS = {"approved", "declined"}
_CORRECTION_OUTCOMES = {"edit_selection", "declined"}


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class UXMetricsCollector:
    """Ring-buffer backed collector for UX quality signals."""

    def __init__(
        self,
        *,
        max_events: int = 500,
        rolling_window_size: int = 50,
    ) -> None:
        # New event-based storage
        self._max_events = max(1, max_events)
        self._approval_events: list[ApprovalEvent] = []
        self._batch_events: list[BatchEvent] = []

        # Legacy scope-based storage
        self._rolling_window_size = max(1, rolling_window_size)
        self._decision_times_by_scope: dict[str, list[float]] = {}
        self._batch_taps_by_scope: dict[str, list[int]] = {}
        self._outcomes_by_scope: dict[str, list[str]] = {}
        self._free_text_modes_by_scope: dict[str, list[bool]] = {}
        self._fatigue_triggers_by_scope: dict[str, int] = {}

    # ── New event-based API (§0.5.6) ──────────────────────────────────────

    def record_approval_decision(
        self,
        token_id: str,
        decision: str,
        duration_ms: int,
    ) -> None:
        """Call after every user approve/decline action."""
        event = ApprovalEvent(
            token_id=token_id,
            decision=decision,
            duration_ms=max(0, duration_ms),
            recorded_at=datetime.now(UTC),
        )
        self._approval_events.append(event)
        self._trim_events(self._approval_events)

    def record_batch_interaction(
        self,
        batch_id: str,
        taps: int,
        items: int,
    ) -> None:
        """Call after a batch review UI interaction completes."""
        event = BatchEvent(
            batch_id=batch_id,
            taps=max(0, taps),
            items=max(1, items),
            recorded_at=datetime.now(UTC),
        )
        self._batch_events.append(event)
        self._trim_events(self._batch_events)

    def get_metrics_summary(self, window_hours: int = 24) -> UXMetricsSummary:
        """Aggregate metrics over the most recent *window_hours*."""
        cutoff = datetime.now(UTC) - timedelta(hours=window_hours)

        approvals = [e for e in self._approval_events if e.recorded_at >= cutoff]
        batches = [e for e in self._batch_events if e.recorded_at >= cutoff]

        total = len(approvals)
        if total == 0:
            return UXMetricsSummary(window_hours=window_hours)

        approved_count = sum(1 for e in approvals if e.decision == "approved")
        declined_count = sum(1 for e in approvals if e.decision == "declined")
        durations = [e.duration_ms for e in approvals]

        total_taps = sum(b.taps for b in batches)
        total_items = sum(b.items for b in batches)

        return UXMetricsSummary(
            window_hours=window_hours,
            total_decisions=total,
            median_decision_time_ms=float(median(durations)),
            approval_rate=approved_count / total,
            decline_rate=declined_count / total,
            batch_taps_per_item=(total_taps / total_items) if total_items else 0.0,
            decisions_per_hour=total / max(window_hours, 1),
            fatigue_score=self._compute_fatigue(durations),
        )

    # ── Legacy scope-based API (backward compat) ──────────────────────────

    def record_decision(
        self,
        scope_id: str,
        requested_at: datetime,
        decided_at: datetime,
        *,
        verdict: str = "approved",
    ) -> None:
        _require_timezone_aware(requested_at, "requested_at")
        _require_timezone_aware(decided_at, "decided_at")
        if verdict not in _DECISION_VERDICTS:
            raise ValueError("verdict must be one of: approved, declined")

        decision_seconds = max((decided_at - requested_at).total_seconds(), 0.0)
        decision_times = self._decision_times_by_scope.setdefault(scope_id, [])
        decision_times.append(decision_seconds)
        self._trim_window(decision_times)

        outcomes = self._outcomes_by_scope.setdefault(scope_id, [])
        outcomes.append(verdict)
        self._trim_window(outcomes)

    def record_batch(self, scope_id: str, taps: int) -> None:
        if taps < 0:
            raise ValueError("taps must be >= 0")

        batch_taps = self._batch_taps_by_scope.setdefault(scope_id, [])
        batch_taps.append(taps)
        self._trim_window(batch_taps)

    def record_correction(self, scope_id: str, *, outcome: str = "edit_selection") -> None:
        if outcome not in _CORRECTION_OUTCOMES:
            raise ValueError("outcome must be one of: edit_selection, declined")

        outcomes = self._outcomes_by_scope.setdefault(scope_id, [])
        outcomes.append(outcome)
        self._trim_window(outcomes)

    def record_undo(self, scope_id: str) -> None:
        outcomes = self._outcomes_by_scope.setdefault(scope_id, [])
        outcomes.append("undo")
        self._trim_window(outcomes)

    def record_fatigue_trigger(self, scope_id: str) -> None:
        self._fatigue_triggers_by_scope[scope_id] = (
            self._fatigue_triggers_by_scope.get(scope_id, 0) + 1
        )

    def record_free_text(self, scope_id: str, *, used_free_text: bool = True) -> None:
        modes = self._free_text_modes_by_scope.setdefault(scope_id, [])
        modes.append(used_free_text)
        self._trim_window(modes)

    def get_metrics(self, scope_id: str) -> dict[str, float | int | str | datetime | None]:
        decision_times = self._decision_times_by_scope.get(scope_id, [])
        batch_taps = self._batch_taps_by_scope.get(scope_id, [])
        outcomes = self._outcomes_by_scope.get(scope_id, [])
        free_text_modes = self._free_text_modes_by_scope.get(scope_id, [])

        total_outcomes = len(outcomes)
        decline_count = sum(1 for outcome in outcomes if outcome == "declined")
        undo_count = sum(1 for outcome in outcomes if outcome == "undo")
        correction_count = sum(
            1 for outcome in outcomes if outcome in {"declined", "edit_selection", "undo"}
        )

        total_selection_events = len(free_text_modes)
        free_text_count = sum(1 for used in free_text_modes if used)

        return {
            "scope_id": scope_id,
            "decision_time": float(median(decision_times)) if decision_times else None,
            "taps_per_batch": float(mean(batch_taps)) if batch_taps else 0.0,
            "decline_rate": (decline_count / total_outcomes) if total_outcomes else 0.0,
            "correction_rate": (correction_count / total_outcomes) if total_outcomes else 0.0,
            "undo_rate": (undo_count / total_outcomes) if total_outcomes else 0.0,
            "approval_fatigue_triggers": self._fatigue_triggers_by_scope.get(scope_id, 0),
            "free_text_usage_rate": (free_text_count / total_selection_events)
            if total_selection_events
            else 0.0,
            "checked_at": datetime.now(UTC),
        }

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _compute_fatigue(durations: list[int]) -> float:
        """Detect trend of increasing decision times.

        Compares average of the second half to the first half.  A large
        increase implies cognitive fatigue.  Result clamped to [0, 1].
        """
        if len(durations) < 4:
            return 0.0

        mid = len(durations) // 2
        first_half_avg = sum(durations[:mid]) / mid
        second_half_avg = sum(durations[mid:]) / (len(durations) - mid)

        if first_half_avg <= 0:
            return 0.0

        # Ratio of slowdown; 2x = fatigue 1.0
        ratio = (second_half_avg - first_half_avg) / first_half_avg
        return max(0.0, min(1.0, ratio))

    def _trim_events(self, buf: list[ApprovalEvent] | list[BatchEvent]) -> None:  # type: ignore[type-arg]
        overflow = len(buf) - self._max_events
        if overflow > 0:
            del buf[:overflow]

    def _trim_window(self, values: list[float] | list[int] | list[str] | list[bool]) -> None:
        overflow = len(values) - self._rolling_window_size
        if overflow > 0:
            del values[:overflow]


__all__ = ["UXMetricsCollector"]
