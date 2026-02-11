from __future__ import annotations

import uuid
from datetime import datetime, timezone

_CORRECTION_OUTCOMES = {"edit_selection", "declined", "undo"}


class SimpleAutonomyCalibrator:
    def __init__(
        self,
        *,
        window_size: int = 25,
        min_sample_size: int = 5,
        widen_threshold: float = 0.10,
        tighten_threshold: float = 0.35,
    ) -> None:
        self._window_size = window_size
        self._min_sample_size = min_sample_size
        self._widen_threshold = widen_threshold
        self._tighten_threshold = tighten_threshold
        self._events_by_scope: dict[str, list[dict[str, object]]] = {}

    async def record_outcome(self, scope_id: str, action_family: str, outcome: str) -> None:
        events = self._events_by_scope.setdefault(scope_id, [])
        events.append(
            {
                "recorded_at": datetime.now(timezone.utc),
                "action_family": action_family,
                "outcome": outcome,
            }
        )
        if len(events) > self._window_size:
            del events[: len(events) - self._window_size]

    async def evaluate(self, scope_id: str, now: datetime) -> list[dict[str, object]]:
        events = self._events_by_scope.get(scope_id, [])
        if len(events) < self._min_sample_size:
            return []

        corrections = sum(
            1
            for event in events
            if isinstance(event.get("outcome"), str) and event["outcome"] in _CORRECTION_OUTCOMES
        )
        correction_rate = corrections / len(events)

        direction: str | None = None
        if correction_rate <= self._widen_threshold:
            direction = "widen"
        elif correction_rate >= self._tighten_threshold:
            direction = "tighten"
        if direction is None:
            return []

        return [
            {
                "proposal_id": f"autonomy:{scope_id}:{uuid.uuid4().hex}",
                "scope_id": scope_id,
                "direction": direction,
                "sample_size": len(events),
                "correction_rate": round(correction_rate, 4),
                "created_at": now,
            }
        ]


__all__ = ["SimpleAutonomyCalibrator"]
