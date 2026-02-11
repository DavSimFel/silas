from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from silas.models.proactivity import SuggestionProposal
from silas.models.work import WorkItemResult


@runtime_checkable
class SuggestionEngine(Protocol):
    async def generate_idle(self, scope_id: str, now: datetime) -> list[SuggestionProposal]: ...

    async def generate_post_execution(
        self,
        scope_id: str,
        result: WorkItemResult,
    ) -> list[SuggestionProposal]: ...

    async def mark_handled(self, scope_id: str, suggestion_id: str, outcome: str) -> None: ...


@runtime_checkable
class AutonomyCalibrator(Protocol):
    async def record_outcome(self, scope_id: str, action_family: str, outcome: str) -> None: ...

    async def evaluate(self, scope_id: str, now: datetime) -> list[dict[str, object]]: ...

    def rollback(self, scope_id: str, action_family: str) -> None: ...

    def get_metrics(self, scope_id: str) -> dict[str, object]: ...


__all__ = ["SuggestionEngine", "AutonomyCalibrator"]
