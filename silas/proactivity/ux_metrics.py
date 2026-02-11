from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, median

_DECISION_VERDICTS = {"approved", "declined"}
_CORRECTION_OUTCOMES = {"edit_selection", "declined"}


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class UXMetricsCollector:
    def __init__(self, *, rolling_window_size: int = 50) -> None:
        self._rolling_window_size = max(1, rolling_window_size)
        self._decision_times_by_scope: dict[str, list[float]] = {}
        self._batch_taps_by_scope: dict[str, list[int]] = {}
        self._outcomes_by_scope: dict[str, list[str]] = {}
        self._free_text_modes_by_scope: dict[str, list[bool]] = {}
        self._fatigue_triggers_by_scope: dict[str, int] = {}

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
        self._fatigue_triggers_by_scope[scope_id] = self._fatigue_triggers_by_scope.get(scope_id, 0) + 1

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
            1
            for outcome in outcomes
            if outcome in {"declined", "edit_selection", "undo"}
        )

        total_selection_events = len(free_text_modes)
        free_text_count = sum(1 for used_free_text in free_text_modes if used_free_text)

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
            "checked_at": datetime.now(timezone.utc),
        }

    def _trim_window(self, values: list[float] | list[int] | list[str] | list[bool]) -> None:
        overflow = len(values) - self._rolling_window_size
        if overflow > 0:
            del values[:overflow]


__all__ = ["UXMetricsCollector"]
