"""Bridge between the Stream turn pipeline and the queue-based agent system.

The bridge is the integration seam: Stream calls it instead of direct agent
invocations, and it translates those calls into queue messages. This lets
us incrementally migrate from procedural agent calls to queue-based dispatch
without rewriting Stream in one shot.

Why a bridge instead of modifying Stream directly: Stream is ~800 lines of
carefully tested orchestration. Inserting queue logic directly would make it
harder to maintain backward compatibility and would bloat an already large file.
The bridge encapsulates all queue interaction behind three methods.
"""

from __future__ import annotations

import asyncio
import logging

from silas.models.messages import TaintLevel
from silas.queue.orchestrator import QueueOrchestrator
from silas.queue.router import QueueRouter
from silas.queue.store import DurableQueueStore
from silas.queue.types import QueueMessage

logger = logging.getLogger(__name__)

# Why 0.1s poll interval: collect_response needs to check frequently for
# responsiveness, but not so fast that it hammers SQLite. 100ms is a good
# balance — users won't notice the latency, and SQLite handles it easily.
_POLL_INTERVAL_S = 0.1


class QueueBridge:
    """Bridges Stream turn pipeline with queue-based agent system.

    Intercepts turns for queue dispatch, lets non-queue turns use the
    legacy direct-call path. The bridge owns no consumers — those are
    managed by the QueueOrchestrator. It only enqueues messages and
    polls for responses.
    """

    def __init__(
        self,
        orchestrator: QueueOrchestrator,
        router: QueueRouter,
        store: DurableQueueStore,
    ) -> None:
        self._orchestrator = orchestrator
        self._router = router
        self._store = store

    @property
    def orchestrator(self) -> QueueOrchestrator:
        """Access the underlying orchestrator for lifecycle management."""
        return self._orchestrator

    async def dispatch_turn(
        self,
        user_message: str,
        trace_id: str,
        metadata: dict[str, object] | None = None,
        *,
        scope_id: str | None = None,
        taint: str | None = None,
        tool_allowlist: list[str] | None = None,
    ) -> None:
        """Enqueue a user message to proxy_queue for agent processing.

        This is the primary entry point for Stream integration. Instead of
        calling proxy.run() directly, Stream calls this to route through
        the queue system.
        """
        payload: dict[str, object] = {"text": user_message}
        if metadata is not None:
            payload["metadata"] = metadata

        msg = QueueMessage(
            message_kind="user_message",
            sender="user",
            trace_id=trace_id,
            payload=payload,
            scope_id=scope_id,
            taint=TaintLevel(taint) if taint else None,
            tool_allowlist=list(tool_allowlist or []),
        )
        await self._router.route(msg)
        logger.debug("Dispatched user_message to queue, trace_id=%s", trace_id)

    async def dispatch_goal(
        self,
        goal_id: str,
        goal_description: str,
        trace_id: str,
    ) -> None:
        """Enqueue a plan_request for autonomous goal execution.

        Used by the scheduler for standing-approved goals that don't
        originate from a user message. Goes directly to planner_queue,
        bypassing proxy.
        """
        msg = QueueMessage(
            message_kind="plan_request",
            sender="runtime",
            trace_id=trace_id,
            payload={
                "user_request": goal_description,
                "goal_id": goal_id,
                "autonomous": True,
            },
        )
        await self._router.route(msg)
        logger.debug("Dispatched goal plan_request, goal_id=%s, trace_id=%s", goal_id, trace_id)

    async def collect_response(
        self,
        trace_id: str,
        timeout_s: float = 120.0,
    ) -> QueueMessage | None:
        """Poll proxy_queue for an agent_response matching the given trace_id.

        Why polling instead of a callback: the queue store is SQLite-backed
        with no notification mechanism. Polling with a short interval is
        the simplest correct approach for our throughput requirements.

        Returns None if no matching response arrives within timeout_s.
        """
        elapsed = 0.0
        while elapsed < timeout_s:
            # Why lease_filtered instead of lease+nack: the old pattern leased
            # ANY message then nacked non-matches back, which is O(n) in queue
            # depth and causes message reordering under concurrent traces.
            # Filtered lease only touches messages belonging to this trace.
            msg = await self._store.lease_filtered(
                queue_name="proxy_queue",
                filter_trace_id=trace_id,
                filter_message_kind="agent_response",
                lease_duration_s=5,
            )
            if msg is not None:
                await self._store.ack(msg.id)
                return msg

            await asyncio.sleep(_POLL_INTERVAL_S)
            elapsed += _POLL_INTERVAL_S

        logger.warning(
            "collect_response timed out after %.1fs for trace_id=%s",
            timeout_s,
            trace_id,
        )
        return None


__all__ = ["QueueBridge"]
