"""Consult-planner suspend/resume for executor→planner guidance flow.

When the executor is stuck, it can suspend and ask the planner for guidance.
This manager handles the full lifecycle: enqueue a guidance request to the
planner, then poll the runtime_queue for the planner's response.

Why separate from consumers: the consult flow crosses queue boundaries
(executor→planner→runtime) and needs stateful wait logic that doesn't
fit in the poll-once consumer model. It's a request-response pattern
layered on top of a message bus.

Spec reference: §5.2.3 — consult-planner suspend/resume.
"""

from __future__ import annotations

import asyncio
import logging
import time

from silas.queue.router import QueueRouter
from silas.queue.store import DurableQueueStore
from silas.queue.types import QueueMessage

logger = logging.getLogger(__name__)

# Why 0.5s poll interval: the consult flow is synchronous from the executor's
# perspective (it's waiting for a response), so we poll more aggressively
# than the regular consumers. 0.5s keeps latency reasonable without hammering.
_CONSULT_POLL_INTERVAL_S = 0.5


class ConsultPlannerManager:
    """Manages the executor→planner consult flow (spec §5.2.3).

    Lifecycle:
    1. Executor calls consult() with failure context
    2. Manager enqueues a planner_guidance request to planner_queue
    3. Manager polls runtime_queue for planner_guidance response (90s timeout)
    4. On response: returns guidance string to executor for retry
    5. On timeout: returns None, executor continues its retry policy

    The runtime_queue is used for the response because planner_guidance
    messages route there per the ROUTE_TABLE. The manager filters by
    trace_id to find the matching response.
    """

    def __init__(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
    ) -> None:
        self._store = store
        self._router = router

    async def consult(
        self,
        work_item_id: str,
        failure_context: str,
        trace_id: str,
        timeout_s: float = 90.0,
    ) -> str | None:
        """Suspend executor, ask planner for guidance, wait for response.

        Returns the guidance string if the planner responds within timeout,
        or None if the timeout expires. The caller (executor consumer or
        LiveWorkItemExecutor) decides what to do with None.
        """
        # Step 1: Enqueue the guidance request to planner.
        request_msg = QueueMessage(
            message_kind="plan_request",
            sender="executor",
            trace_id=trace_id,
            payload={
                "user_request": (
                    f"CONSULT REQUEST — executor needs guidance.\n\n"
                    f"Work item: {work_item_id}\n"
                    f"Failure context:\n{failure_context}\n\n"
                    f"Provide specific, actionable guidance for retrying this work item."
                ),
                "consult": True,
                "work_item_id": work_item_id,
            },
        )
        await self._router.route(request_msg)
        logger.info(
            "Consult request sent for work_item=%s trace=%s",
            work_item_id,
            trace_id,
        )

        # Step 2: Poll runtime_queue for the planner_guidance response.
        # Why polling: we can't subscribe to a specific message. We lease
        # messages and check if they match our trace_id.
        deadline = time.monotonic() + timeout_s
        consumer_name = f"consult:{work_item_id}"

        while time.monotonic() < deadline:
            msg = await self._store.lease("runtime_queue")
            if msg is None:
                await asyncio.sleep(_CONSULT_POLL_INTERVAL_S)
                continue

            # Check if this is our response.
            if msg.message_kind == "planner_guidance" and msg.trace_id == trace_id:
                guidance = str(msg.payload.get("guidance", ""))
                await self._store.mark_processed(consumer_name, msg.id)
                await self._store.ack(msg.id)
                logger.info(
                    "Consult response received for work_item=%s",
                    work_item_id,
                )
                return guidance

            # Not our message — nack it so another consumer can pick it up.
            # Why nack instead of ack: we didn't process it, another consumer
            # or another consult() call might need it.
            await self._store.nack(msg.id)
            await asyncio.sleep(_CONSULT_POLL_INTERVAL_S)

        logger.warning(
            "Consult timeout for work_item=%s after %.1fs",
            work_item_id,
            timeout_s,
        )
        return None


__all__ = ["ConsultPlannerManager"]
