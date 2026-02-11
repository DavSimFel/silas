from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from silas.models.goals import Goal, GoalRun, StandingApproval


@runtime_checkable
class GoalManager(Protocol):
    def load_goals(self) -> list[Goal]: ...

    def schedule_goal(self, goal: Goal) -> None: ...

    def unschedule_goal(self, goal_id: str) -> None: ...

    def run_goal(self, goal_id: str) -> GoalRun: ...

    def get_standing_approval(self, goal_id: str, policy_hash: str) -> StandingApproval | None: ...

    def grant_standing_approval(
        self,
        goal_id: str,
        policy_hash: str,
        granted_by: str,
        expires_at: datetime | None,
        max_uses: int | None,
    ) -> StandingApproval: ...

    def revoke_standing_approval(self, approval_id: str) -> bool: ...

    def list_runs(self, goal_id: str, limit: int = 50) -> list[GoalRun]: ...


__all__ = ["GoalManager"]
