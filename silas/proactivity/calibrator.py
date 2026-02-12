from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

_CORRECTION_OUTCOMES = {"edit_selection", "declined", "undo"}
_DEFAULT_FAMILY_THRESHOLD = 0.5


class SimpleAutonomyCalibrator:
    def __init__(
        self,
        *,
        window_size: int = 25,
        min_sample_size: int = 5,
        widen_threshold: float = 0.10,
        tighten_threshold: float = 0.35,
        threshold_step: float = 0.1,
    ) -> None:
        self._window_size = window_size
        self._min_sample_size = min_sample_size
        self._widen_threshold = widen_threshold
        self._tighten_threshold = tighten_threshold
        self._threshold_step = threshold_step
        self._events_by_scope: dict[str, list[dict[str, object]]] = {}
        self._thresholds_by_scope: dict[str, dict[str, float]] = {}
        self._change_history_by_scope: dict[str, dict[str, list[float]]] = {}

    async def record_outcome(self, scope_id: str, action_family: str, outcome: str) -> None:
        events = self._events_by_scope.setdefault(scope_id, [])
        events.append(
            {
                "recorded_at": datetime.now(UTC),
                "action_family": action_family,
                "outcome": outcome,
            }
        )
        if len(events) > self._window_size:
            del events[: len(events) - self._window_size]

    async def evaluate(self, scope_id: str, now: datetime) -> list[dict[str, object]]:
        events = self._events_by_scope.get(scope_id, [])
        family_events: dict[str, list[dict[str, object]]] = {}
        for event in events:
            action_family = event.get("action_family")
            if not isinstance(action_family, str):
                continue
            family_events.setdefault(action_family, []).append(event)

        proposals: list[dict[str, object]] = []
        for action_family, grouped_events in family_events.items():
            if len(grouped_events) < self._min_sample_size:
                continue

            corrections = sum(
                1
                for event in grouped_events
                if isinstance(event.get("outcome"), str) and event["outcome"] in _CORRECTION_OUTCOMES
            )
            correction_rate = corrections / len(grouped_events)

            direction: str | None = None
            if correction_rate <= self._widen_threshold:
                direction = "widen"
            elif correction_rate >= self._tighten_threshold:
                direction = "tighten"
            if direction is None:
                continue

            previous_threshold = self._current_threshold(scope_id, action_family)
            delta = self._threshold_step if direction == "widen" else -self._threshold_step
            next_threshold = min(1.0, max(0.0, round(previous_threshold + delta, 4)))
            self._set_threshold(scope_id, action_family, next_threshold)
            self._push_threshold_change(scope_id, action_family, previous_threshold)

            proposals.append(
                {
                    "proposal_id": f"autonomy:{scope_id}:{uuid.uuid4().hex}",
                    "scope_id": scope_id,
                    "action_family": action_family,
                    "direction": direction,
                    "sample_size": len(grouped_events),
                    "correction_rate": round(correction_rate, 4),
                    "created_at": now,
                }
            )

        return proposals

    async def apply(self, proposal: dict[str, object], decision: str) -> dict[str, object]:
        """Finalize a proposal decision and optionally roll back rejected changes.

        `evaluate()` already mutates thresholds optimistically, so `approved`
        only acknowledges the change while `rejected` must restore the previous
        threshold for the proposal's scope/family pair.
        """
        scope_id, action_family = self._extract_target_from_proposal(proposal)
        normalized_decision = decision.strip().lower()

        if normalized_decision == "approved":
            return {
                "proposal_id": proposal.get("proposal_id"),
                "scope_id": scope_id,
                "action_family": action_family,
                "decision": "approved",
                "rolled_back": False,
            }

        if normalized_decision == "rejected":
            self.rollback(scope_id, action_family)
            return {
                "proposal_id": proposal.get("proposal_id"),
                "scope_id": scope_id,
                "action_family": action_family,
                "decision": "rejected",
                "rolled_back": True,
            }

        raise ValueError("decision must be 'approved' or 'rejected'")

    def rollback(self, scope_id: str, action_family: str) -> None:
        history = self._change_history_by_scope.get(scope_id, {}).get(action_family)
        if not history:
            return

        previous_threshold = history.pop()
        self._set_threshold(scope_id, action_family, previous_threshold)

    def get_metrics(self, scope_id: str) -> dict[str, Any]:
        events = self._events_by_scope.get(scope_id, [])
        family_counts: dict[str, dict[str, int]] = {}
        for event in events:
            action_family = event.get("action_family")
            if not isinstance(action_family, str):
                continue

            counts = family_counts.setdefault(action_family, {"sample_size": 0, "corrections": 0})
            counts["sample_size"] += 1
            outcome = event.get("outcome")
            if isinstance(outcome, str) and outcome in _CORRECTION_OUTCOMES:
                counts["corrections"] += 1

        families: dict[str, dict[str, float | int]] = {}
        for action_family, counts in family_counts.items():
            history = self._change_history_by_scope.get(scope_id, {}).get(action_family, [])
            families[action_family] = {
                "sample_size": counts["sample_size"],
                "corrections": counts["corrections"],
                "threshold": self._current_threshold(scope_id, action_family),
                "change_count": len(history),
            }

        return {
            "scope_id": scope_id,
            "total_events": len(events),
            "families": families,
        }

    def _current_threshold(self, scope_id: str, action_family: str) -> float:
        return self._thresholds_by_scope.get(scope_id, {}).get(
            action_family, _DEFAULT_FAMILY_THRESHOLD
        )

    def _set_threshold(self, scope_id: str, action_family: str, threshold: float) -> None:
        scope_thresholds = self._thresholds_by_scope.setdefault(scope_id, {})
        scope_thresholds[action_family] = threshold

    def _push_threshold_change(self, scope_id: str, action_family: str, previous: float) -> None:
        scope_history = self._change_history_by_scope.setdefault(scope_id, {})
        family_history = scope_history.setdefault(action_family, [])
        family_history.append(previous)

    def _extract_target_from_proposal(self, proposal: dict[str, object]) -> tuple[str, str]:
        scope_id = proposal.get("scope_id")
        action_family = proposal.get("action_family")
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise ValueError("proposal.scope_id must be a non-empty string")
        if not isinstance(action_family, str) or not action_family.strip():
            raise ValueError("proposal.action_family must be a non-empty string")
        return scope_id.strip(), action_family.strip()


__all__ = ["SimpleAutonomyCalibrator"]
