"""Queue routing table for the Silas runtime bus.

Maps message_kind to destination queue name per specs/agent-loop-architecture.md
§2.3. The router is the single source of truth for where each message type goes;
agents never hard-code queue names directly.

Why a static table instead of dynamic routing: the routing topology is fixed by
the architecture spec. Dynamic routing would add complexity without benefit and
make message flow harder to reason about.
"""

from __future__ import annotations

from silas.execution.queue_store import DurableQueueStore
from silas.execution.queue_types import QueueMessage

# Why a plain dict: it's the simplest correct representation. The keys are
# exhaustive over MessageKind (enforced by tests). A class or registry would
# add abstraction without value.
ROUTE_TABLE: dict[str, str] = {
    "plan_request": "planner_queue",
    "plan_result": "proxy_queue",
    "execution_request": "executor_queue",
    "execution_status": "proxy_queue",
    "research_request": "executor_queue",
    "research_result": "planner_queue",
    "planner_guidance": "runtime_queue",
    "replan_request": "planner_queue",
    "approval_request": "proxy_queue",
    "approval_result": "runtime_queue",
    "user_message": "proxy_queue",
    "agent_response": "proxy_queue",
    "system_event": "proxy_queue",
}


class QueueRouter:
    """Routes messages to the correct queue based on message_kind.

    Thin layer over DurableQueueStore that sets queue_name from the routing
    table before enqueueing. This keeps routing logic centralized and prevents
    agents from needing to know queue names.
    """

    def __init__(self, store: DurableQueueStore) -> None:
        self._store = store

    async def route(self, msg: QueueMessage) -> None:
        """Set queue_name from the routing table and enqueue.

        Raises KeyError if message_kind is not in the routing table,
        which indicates a programming error (unknown message type).
        """
        msg.queue_name = ROUTE_TABLE[msg.message_kind]
        await self._store.enqueue(msg)

    async def route_with_trace(self, msg: QueueMessage, trace_id: str) -> None:
        """Set trace_id for cross-hop correlation, then route.

        Used when the runtime needs to propagate an existing trace_id
        to a new message (e.g., plan_request derived from user_message).
        """
        msg.trace_id = trace_id
        await self.route(msg)


__all__ = ["ROUTE_TABLE", "QueueRouter"]
