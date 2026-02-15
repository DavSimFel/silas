"""Goal manager — CRUD, event matching, standing approval checks, auto-deactivation.

Bridges goals with the queue bus: when an external event matches a goal's
subscriptions, the manager injects a user_message into the proxy_queue so the
normal pipeline (proxy → planner → executor) handles it. Standing approvals
are checked before injection to decide if the goal can execute autonomously
or needs escalation.
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import UTC, datetime

from silas.models.goals import Goal, GoalRun, GoalSubscription, StandingApproval
from silas.queue.store import DurableQueueStore
from silas.queue.types import QueueMessage

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GoalManager:
    """Manages goals lifecycle: CRUD, event matching, queue injection."""

    def __init__(self, store: DurableQueueStore | None = None) -> None:
        self._goals: dict[str, Goal] = {}
        self._approvals: dict[str, StandingApproval] = {}
        self._runs: dict[str, GoalRun] = {}
        self._store = store

    # ── CRUD ────────────────────────────────────────────────────────

    def register(self, goal: Goal) -> None:
        self._goals[goal.goal_id] = goal

    def get(self, goal_id: str) -> Goal | None:
        return self._goals.get(goal_id)

    def unregister(self, goal_id: str) -> bool:
        return self._goals.pop(goal_id, None) is not None

    def list_goals(self, *, enabled_only: bool = False) -> list[Goal]:
        goals = list(self._goals.values())
        if enabled_only:
            goals = [g for g in goals if g.enabled]
        return goals

    def add_standing_approval(self, approval: StandingApproval) -> None:
        self._approvals[approval.approval_id] = approval

    def record_run(self, run: GoalRun) -> None:
        self._runs[run.run_id] = run

    def get_run(self, run_id: str) -> GoalRun | None:
        return self._runs.get(run_id)

    # ── Event matching ──────────────────────────────────────────────

    def match_event(
        self,
        source: str,
        event_type: str,
        event_data: dict[str, object] | None = None,
    ) -> list[Goal]:
        """Return all enabled goals whose subscriptions match the event."""
        matched: list[Goal] = []
        for goal in self._goals.values():
            if not goal.enabled:
                continue
            for sub in goal.subscriptions:
                if not sub.active:
                    continue
                if self._subscription_matches(sub, source, event_type, event_data):
                    matched.append(goal)
                    break  # one match per goal is enough
        return matched

    @staticmethod
    def _subscription_matches(
        sub: GoalSubscription,
        source: str,
        event_type: str,
        event_data: dict[str, object] | None,
    ) -> bool:
        if sub.source != source:
            return False
        if not fnmatch.fnmatch(event_type, sub.event_type):
            return False
        # Filter: every key in sub.filter must match the event_data value
        if sub.filter and event_data:
            for key, expected in sub.filter.items():
                if event_data.get(key) != expected:
                    return False
        elif sub.filter and not event_data:
            return False
        return True

    # ── Standing approval check ─────────────────────────────────────

    def check_standing_approval(self, goal: Goal) -> StandingApproval | None:
        """Return a valid standing approval for the goal, or None."""
        now = _utc_now()
        for approval_id in goal.standing_approvals:
            approval = self._approvals.get(approval_id)
            if approval is None:
                continue
            if approval.goal_id != goal.goal_id:
                continue
            if approval.expires_at is not None and approval.expires_at <= now:
                continue
            if approval.uses_remaining is not None and approval.uses_remaining <= 0:
                continue
            return approval
        return None

    def consume_approval(self, approval: StandingApproval) -> None:
        """Decrement uses_remaining on a standing approval."""
        if approval.uses_remaining is not None:
            approval.uses_remaining -= 1

    # ── Queue injection ─────────────────────────────────────────────

    async def inject_event(
        self,
        goal: Goal,
        source: str,
        event_type: str,
        event_data: dict[str, object] | None = None,
    ) -> QueueMessage | None:
        """Create a user_message for the matched goal and enqueue it.

        Returns the enqueued message, or None if no store is configured.
        """
        approval = self.check_standing_approval(goal)
        has_approval = approval is not None

        text = (
            f"[Goal: {goal.name}] Event received: {source}/{event_type}.\n\n"
            f"Goal description: {goal.description}\n\n"
            f"Standing approval: {'yes' if has_approval else 'no — escalate for approval'}"
        )

        msg = QueueMessage(
            message_kind="user_message",
            sender="runtime",
            payload={
                "text": text,
                "goal_id": goal.goal_id,
                "event_source": source,
                "event_type": event_type,
                "event_data": event_data or {},
                "has_standing_approval": has_approval,
                "metadata": {
                    "goal_id": goal.goal_id,
                    "goal_name": goal.name,
                    "urgency": goal.urgency,
                },
            },
            urgency=goal.urgency,
        )

        if has_approval and approval is not None:
            self.consume_approval(approval)

        if self._store is not None:
            msg.queue_name = "proxy_queue"
            await self._store.enqueue(msg)

        return msg

    # ── Auto-deactivation ───────────────────────────────────────────

    def deactivate_on_completion(self, goal_id: str) -> bool:
        """Disable a goal and all its subscriptions. Returns True if found."""
        goal = self._goals.get(goal_id)
        if goal is None:
            return False
        goal.enabled = False
        for sub in goal.subscriptions:
            sub.active = False
        goal.updated_at = _utc_now()
        logger.info("Goal %s deactivated on completion", goal_id)
        return True


__all__ = ["GoalManager"]
