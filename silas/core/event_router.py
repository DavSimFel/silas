"""Event router — receives webhook events, routes to GoalManager.

Entry point for external events (webhooks, n8n triggers, etc.). Normalizes
the incoming payload, asks GoalManager to match subscriptions, and injects
matched goals into the proxy_queue as user_messages with sender=runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from silas.core.goal_manager import GoalManager
from silas.queue.types import QueueMessage

logger = logging.getLogger(__name__)


@dataclass
class WebhookEvent:
    """Normalized external event."""

    source: str
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)


class EventRouter:
    """Receives webhook events and routes them through GoalManager."""

    def __init__(self, goal_manager: GoalManager) -> None:
        self._goal_manager = goal_manager

    async def handle_event(self, event: WebhookEvent) -> list[QueueMessage]:
        """Match event against goal subscriptions and inject into queue.

        Returns list of injected QueueMessages (one per matched goal).
        """
        matched_goals = self._goal_manager.match_event(
            source=event.source,
            event_type=event.event_type,
            event_data=event.data,
        )

        if not matched_goals:
            logger.debug(
                "No goals matched event %s/%s",
                event.source,
                event.event_type,
            )
            return []

        logger.info(
            "Event %s/%s matched %d goal(s): %s",
            event.source,
            event.event_type,
            len(matched_goals),
            [g.goal_id for g in matched_goals],
        )

        messages: list[QueueMessage] = []
        for goal in matched_goals:
            msg = await self._goal_manager.inject_event(
                goal=goal,
                source=event.source,
                event_type=event.event_type,
                event_data=event.data,
            )
            if msg is not None:
                messages.append(msg)

        return messages


__all__ = ["EventRouter", "WebhookEvent"]
