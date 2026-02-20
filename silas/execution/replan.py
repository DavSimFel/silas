"""Replan cascade — automatic re-planning when execution exhausts all retries.

Implements Design Principle #8 (spec §4.6.1): the system is built for full
autonomy. When a work item exhausts its retry attempts AND consult-planner
guidance, the replan cascade triggers the planner to create an entirely new
strategy. User escalation is the absolute last resort.

max_replan_depth=2 prevents infinite replan loops. After depth 2, the system
escalates to the user.
"""

from __future__ import annotations

import logging

from silas.execution.queue_types import QueueMessage
from silas.execution.router import QueueRouter

logger = logging.getLogger(__name__)

# Why 2: spec §4.6.1 mandates a maximum replan depth to prevent infinite
# loops. 2 replans means the planner gets 3 total attempts (original + 2
# replans) before we give up and ask the human.
MAX_REPLAN_DEPTH = 2


class ReplanManager:
    """Manages the automatic re-plan cascade (spec §4.6.1).

    Triggered when ALL of: retry attempts exhausted + consult-planner exhausted.
    Enqueues a replan_request to the planner with full failure history so the
    planner can produce an alternative strategy (not retry the same approach).

    After max_replan_depth (2), returns False to signal the caller should
    escalate to the user.
    """

    def __init__(self, router: QueueRouter) -> None:
        self._router = router

    async def trigger_replan(
        self,
        work_item_id: str,
        original_goal: str,
        failure_history: list[dict[str, object]],
        trace_id: str,
        current_depth: int = 0,
    ) -> bool:
        """Trigger a re-plan. Returns True if replan was enqueued, False if max depth exceeded.

        When False is returned, the caller should escalate to the user —
        automated recovery has been fully exhausted.
        """
        if current_depth >= MAX_REPLAN_DEPTH:
            logger.warning(
                "Replan depth %d >= max %d for work_item=%s — escalating to user",
                current_depth,
                MAX_REPLAN_DEPTH,
                work_item_id,
            )
            return False

        msg = QueueMessage(
            message_kind="replan_request",
            sender="runtime",
            trace_id=trace_id,
            payload={
                "work_item_id": work_item_id,
                "original_goal": original_goal,
                "failure_history": failure_history,
                "replan_depth": current_depth + 1,
            },
        )
        await self._router.route(msg)
        logger.info(
            "Replan enqueued for work_item=%s depth=%d",
            work_item_id,
            current_depth + 1,
        )
        return True


__all__ = ["MAX_REPLAN_DEPTH", "ReplanManager"]
