from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from silas.models.proactivity import SuggestionProposal
from silas.models.work import WorkItemResult, WorkItemStatus


class SimpleSuggestionEngine:
    def __init__(self, cooldown: timedelta = timedelta(minutes=15)) -> None:
        self._cooldown = cooldown
        self._idle_by_scope: dict[str, list[SuggestionProposal]] = {}
        self._handled_at: dict[str, dict[str, datetime]] = {}

    def queue_idle(self, scope_id: str, suggestion: SuggestionProposal) -> None:
        self._idle_by_scope.setdefault(scope_id, []).append(suggestion)

    async def generate_idle(self, scope_id: str, now: datetime) -> list[SuggestionProposal]:
        self._prune_expired(scope_id, now)
        handled = self._handled_at.get(scope_id, {})
        proposals: list[SuggestionProposal] = []
        for suggestion in self._idle_by_scope.get(scope_id, []):
            handled_at = handled.get(suggestion.id)
            if handled_at is not None and handled_at + self._cooldown > now:
                continue
            proposals.append(suggestion)
        return proposals

    async def generate_post_execution(
        self,
        scope_id: str,
        result: WorkItemResult,
    ) -> list[SuggestionProposal]:
        now = datetime.now(UTC)
        confidence = 0.75 if result.status == WorkItemStatus.done else 0.55
        expires_at = now + timedelta(hours=12)

        return [
            SuggestionProposal(
                id=f"suggestion:post:{scope_id}:{result.work_item_id}:{index}:{uuid.uuid4().hex}",
                text=step,
                confidence=confidence,
                source="post_execution",
                category="next_step",
                action_hint="Run this next step",
                created_at=now,
                expires_at=expires_at,
            )
            for index, step in enumerate(result.next_steps, start=1)
        ]

    async def mark_handled(self, scope_id: str, suggestion_id: str, outcome: str) -> None:
        del outcome
        self._handled_at.setdefault(scope_id, {})[suggestion_id] = datetime.now(UTC)

    def _prune_expired(self, scope_id: str, now: datetime) -> None:
        suggestions = self._idle_by_scope.get(scope_id, [])
        self._idle_by_scope[scope_id] = [
            suggestion for suggestion in suggestions if suggestion.expires_at > now
        ]


__all__ = ["SimpleSuggestionEngine"]
