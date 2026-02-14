from __future__ import annotations

from datetime import UTC, datetime
from statistics import median

type FatigueStatusValue = str | float | int | bool | datetime | None

_MEDIUM_PLUS_RISK_LEVELS = {"medium", "high", "irreversible"}


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class ApprovalFatigueTracker:
    def __init__(
        self,
        *,
        rolling_window_size: int = 25,
        queue_density_threshold: int = 5,
    ) -> None:
        self._rolling_window_size = max(1, rolling_window_size)
        self._queue_density_threshold = queue_density_threshold

        self._decision_times_by_scope: dict[str, list[float]] = {}
        self._decision_timestamps_by_scope: dict[str, list[datetime]] = {}
        self._queue_density_cue_active: dict[str, bool] = {}
        self._decision_time_cue_active: dict[str, bool] = {}
        self._fatigue_triggers_by_scope: dict[str, int] = {}

    def record_decision(
        self,
        scope_id: str,
        risk_level: str,
        requested_at: datetime,
        decided_at: datetime,
    ) -> dict[str, FatigueStatusValue]:
        _require_timezone_aware(requested_at, "requested_at")
        _require_timezone_aware(decided_at, "decided_at")

        if risk_level not in _MEDIUM_PLUS_RISK_LEVELS:
            return self.status(scope_id)

        decision_seconds = max((decided_at - requested_at).total_seconds(), 0.0)
        decision_times = self._decision_times_by_scope.setdefault(scope_id, [])
        decision_times.append(decision_seconds)
        self._trim_window(decision_times)

        decision_timestamps = self._decision_timestamps_by_scope.setdefault(scope_id, [])
        decision_timestamps.append(decided_at)
        self._trim_window(decision_timestamps)

        median_seconds = self.median_decision_time(scope_id)
        decision_time_cue = bool(median_seconds is not None and median_seconds < 1.0)
        if decision_time_cue and not self._decision_time_cue_active.get(scope_id, False):
            self._fatigue_triggers_by_scope[scope_id] = self._fatigue_triggers_by_scope.get(scope_id, 0) + 1
        self._decision_time_cue_active[scope_id] = decision_time_cue

        return self.status(scope_id)

    def record_queue_depth(self, scope_id: str, medium_plus_pending: int) -> bool:
        queue_density_cue = medium_plus_pending > self._queue_density_threshold
        previously_active = self._queue_density_cue_active.get(scope_id, False)
        if queue_density_cue and not previously_active:
            self._fatigue_triggers_by_scope[scope_id] = self._fatigue_triggers_by_scope.get(scope_id, 0) + 1
        self._queue_density_cue_active[scope_id] = queue_density_cue
        return queue_density_cue

    def median_decision_time(self, scope_id: str) -> float | None:
        values = self._decision_times_by_scope.get(scope_id, [])
        if not values:
            return None
        return float(median(values))

    def cadence_per_minute(self, scope_id: str) -> float:
        timestamps = self._decision_timestamps_by_scope.get(scope_id, [])
        if len(timestamps) < 2:
            return 0.0
        span_seconds = (timestamps[-1] - timestamps[0]).total_seconds()
        span_seconds = max(span_seconds, 1.0)
        cadence = len(timestamps) / (span_seconds / 60.0)
        return round(cadence, 4)

    def fatigue_trigger_count(self, scope_id: str) -> int:
        return self._fatigue_triggers_by_scope.get(scope_id, 0)

    def should_throttle(self, scope_id: str) -> bool:
        del scope_id
        # Explicitly non-blocking by spec: no mandatory delays/cooldowns.
        return False

    def status(
        self,
        scope_id: str,
        medium_plus_pending: int = 0,
    ) -> dict[str, FatigueStatusValue]:
        median_seconds = self.median_decision_time(scope_id)
        return {
            "scope_id": scope_id,
            "queue_density_cue": medium_plus_pending > self._queue_density_threshold,
            "decision_time_fatigue_cue": bool(median_seconds is not None and median_seconds < 1.0),
            "median_decision_time_seconds": median_seconds,
            "cadence_decisions_per_minute": self.cadence_per_minute(scope_id),
            "fatigue_trigger_count": self.fatigue_trigger_count(scope_id),
            "hard_throttle": False,
            "checked_at": datetime.now(UTC),
        }

    def _trim_window(self, values: list[float] | list[datetime]) -> None:
        overflow = len(values) - self._rolling_window_size
        if overflow > 0:
            del values[:overflow]


__all__ = ["ApprovalFatigueTracker"]
