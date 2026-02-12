"""Agent queue consumers — lease/process/ack lifecycle for each agent.

Each agent (proxy, planner, executor) gets a consumer subclass that leases
messages from its queue, dispatches to the agent, and routes results onward.
All consumers share the same base lease management pattern via BaseConsumer.

Why polling: SQLite has no LISTEN/NOTIFY. Polling with exponential backoff
is simpler and sufficient for our throughput (<100 msgs/sec).

Why a base class: DRY on lease/ack/nack/dead-letter without over-abstraction.
Subclasses only implement _process() to define agent-specific dispatch logic.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from silas.queue.router import QueueRouter
from silas.queue.status_router import route_to_surface
from silas.queue.store import DurableQueueStore
from silas.queue.types import QueueMessage

logger = logging.getLogger(__name__)

# Why 5: matches the default max_attempts in DurableQueueStore schema.
# Consumers should not silently diverge from the store's default.
_DEFAULT_MAX_ATTEMPTS = 5


@runtime_checkable
class ProxyAgentProtocol(Protocol):
    """Minimal interface for the proxy agent needed by ProxyConsumer.

    Why a protocol: decouples consumers from concrete agent classes,
    enabling mock injection in tests without importing pydantic-ai.
    """

    async def run(self, prompt: str, deps: object | None = None) -> object: ...


@runtime_checkable
class PlannerAgentProtocol(Protocol):
    """Minimal interface for the planner agent needed by PlannerConsumer."""

    async def run(self, prompt: str, deps: object | None = None) -> object: ...


@runtime_checkable
class ExecutorAgentProtocol(Protocol):
    """Minimal interface for the executor agent needed by ExecutorConsumer."""

    async def run(self, prompt: str, deps: object | None = None) -> object: ...


class BaseConsumer:
    """Base class for queue consumers. Handles lease/ack/nack/dead-letter lifecycle.

    All consumers share the same lease management pattern. The only difference
    is which agent processes the message and how results are routed. Subclasses
    implement _process() to define their dispatch logic.
    """

    def __init__(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        queue_name: str,
        *,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self._store = store
        self._router = router
        self._queue_name = queue_name
        self._max_attempts = max_attempts
        # Why consumer name derived from queue: used as the idempotency
        # key in has_processed/mark_processed. Each queue has one consumer.
        self._consumer_name = f"consumer:{queue_name}"

    @property
    def queue_name(self) -> str:
        """The queue this consumer reads from."""
        return self._queue_name

    async def poll_once(self) -> bool:
        """Lease one message, process it, ack/nack. Returns True if a message was processed.

        Why poll-once instead of a loop: the orchestrator controls the polling
        cadence and backoff. This method is a single atomic unit of work.
        """
        msg = await self._store.lease(self._queue_name)
        if msg is None:
            return False

        # Idempotency check: skip if we already processed this message
        # (possible after crash where ack was lost but mark_processed succeeded).
        if await self._store.has_processed(self._consumer_name, msg.id):
            await self._store.ack(msg.id)
            return True

        # Dead-letter check: if we've exhausted attempts, don't try again.
        if msg.attempt_count >= self._max_attempts:
            await self._store.dead_letter(
                msg.id, f"max_attempts_exceeded ({self._max_attempts})"
            )
            logger.warning(
                "Dead-lettered message %s after %d attempts",
                msg.id, msg.attempt_count,
            )
            return True

        try:
            response = await self._process(msg)
            await self._store.mark_processed(self._consumer_name, msg.id)
            await self._store.ack(msg.id)

            # Route the response message onward if the processor produced one.
            if response is not None:
                await self._router.route(response)
            return True

        except Exception:
            logger.exception("Consumer %s failed processing message %s", self._consumer_name, msg.id)
            await self._store.nack(msg.id)
            return True

    async def _process(self, msg: QueueMessage) -> QueueMessage | None:
        """Subclasses implement this. Returns a response message to route, or None."""
        raise NotImplementedError


class ProxyConsumer(BaseConsumer):
    """Consumes from proxy_queue. Dispatches to ProxyAgent, routes results.

    Handles: user_message, plan_result, execution_status, approval_request,
    agent_response, system_event.

    For user_message: runs proxy agent → if route=planner, enqueues plan_request.
    For execution_status: extracts StatusPayload, routes to appropriate UI surface.
    For plan_result: presents plan to user for approval.
    """

    def __init__(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        proxy_agent: ProxyAgentProtocol,
        *,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        super().__init__(store, router, "proxy_queue", max_attempts=max_attempts)
        self._proxy = proxy_agent

    async def _process(self, msg: QueueMessage) -> QueueMessage | None:
        """Dispatch based on message_kind and produce response messages."""
        kind = msg.message_kind

        if kind == "user_message":
            return await self._handle_user_message(msg)
        if kind == "execution_status":
            return self._handle_execution_status(msg)
        if kind == "plan_result":
            return self._handle_plan_result(msg)

        # For agent_response, approval_request, system_event: run proxy
        # and return whatever it says. These are informational messages
        # that the proxy surfaces to the user.
        return await self._handle_generic(msg)

    async def _handle_user_message(self, msg: QueueMessage) -> QueueMessage | None:
        """Run proxy agent on user message. Route to planner if needed."""
        prompt = str(msg.payload.get("text", ""))
        result = await self._proxy.run(prompt)

        # Why getattr: ProxyRunResult has .output.route, but we use a
        # protocol so we access it generically to stay decoupled.
        output = getattr(result, "output", None)
        route = getattr(output, "route", "direct")

        if route == "planner":
            return QueueMessage(
                message_kind="plan_request",
                sender="proxy",
                trace_id=msg.trace_id,
                payload={"user_request": prompt, "reason": getattr(output, "reason", "")},
            )

        # Direct response: no further routing needed.
        return None

    def _handle_execution_status(self, msg: QueueMessage) -> QueueMessage | None:
        """Route execution status to appropriate UI surfaces.

        Why no async: status routing is a pure data transform with no I/O.
        The actual UI notification happens downstream.
        """
        status_str = str(msg.payload.get("status", ""))
        surfaces = route_to_surface(status_str)

        # Attach surface routing info to the payload so downstream consumers
        # know where to deliver the notification.
        enriched_payload = dict(msg.payload)
        enriched_payload["surfaces"] = list(surfaces)

        # Status messages terminate here — they're informational, not routable
        # to another agent queue.
        return None

    def _handle_plan_result(self, msg: QueueMessage) -> QueueMessage | None:
        """Plan results are presented to the user for approval.

        The actual UI presentation is handled by the stream/channel layer.
        We just acknowledge receipt here — no further queue routing needed.
        """
        return None

    async def _handle_generic(self, msg: QueueMessage) -> QueueMessage | None:
        """Run proxy on informational messages (agent_response, system_event, etc.)."""
        prompt = str(msg.payload.get("text", msg.payload.get("message", "")))
        await self._proxy.run(prompt)
        return None


class PlannerConsumer(BaseConsumer):
    """Consumes from planner_queue. Dispatches to PlannerAgent.

    Handles: plan_request, research_result, replan_request.

    For plan_request: runs planner → produces plan → enqueues plan_result.
    For research_result: feeds result into planner's research state machine.
    For replan_request: runs planner with failure context → enqueues revised plan_result.
    """

    def __init__(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        planner_agent: PlannerAgentProtocol,
        *,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        super().__init__(store, router, "planner_queue", max_attempts=max_attempts)
        self._planner = planner_agent

    async def _process(self, msg: QueueMessage) -> QueueMessage | None:
        """Dispatch planner messages and produce plan_result responses."""
        kind = msg.message_kind

        if kind == "plan_request":
            return await self._handle_plan_request(msg)
        if kind == "replan_request":
            return await self._handle_replan_request(msg)
        if kind == "research_result":
            return await self._handle_research_result(msg)

        # Unknown kinds get logged and dropped.
        logger.warning("PlannerConsumer received unexpected kind: %s", kind)
        return None

    async def _handle_plan_request(self, msg: QueueMessage) -> QueueMessage:
        """Run planner on user request and produce a plan_result."""
        user_request = str(msg.payload.get("user_request", ""))
        result = await self._planner.run(user_request)
        output = getattr(result, "output", None)

        plan_markdown = ""
        message = ""
        if output is not None:
            plan_action = getattr(output, "plan_action", None)
            if plan_action is not None:
                plan_markdown = getattr(plan_action, "plan_markdown", "") or ""
            message = getattr(output, "message", "") or ""

        return QueueMessage(
            message_kind="plan_result",
            sender="planner",
            trace_id=msg.trace_id,
            payload={
                "plan_markdown": plan_markdown,
                "message": message,
                "user_request": user_request,
            },
        )

    async def _handle_replan_request(self, msg: QueueMessage) -> QueueMessage:
        """Run planner with failure context to produce a revised plan.

        Why separate from plan_request: the prompt includes failure history
        so the planner knows what was already tried and must produce an
        alternative strategy, not retry the same approach.
        """
        original_goal = str(msg.payload.get("original_goal", ""))
        failure_history = msg.payload.get("failure_history", [])
        # Why explicit prompt construction: the planner needs to see the
        # full failure context to generate a meaningfully different plan.
        prompt = (
            f"REPLAN REQUEST — previous approach failed.\n\n"
            f"Original goal: {original_goal}\n\n"
            f"Failure history:\n{failure_history}\n\n"
            f"Generate an alternative strategy. Do NOT retry the same approach."
        )
        result = await self._planner.run(prompt)
        output = getattr(result, "output", None)

        plan_markdown = ""
        message = ""
        if output is not None:
            plan_action = getattr(output, "plan_action", None)
            if plan_action is not None:
                plan_markdown = getattr(plan_action, "plan_markdown", "") or ""
            message = getattr(output, "message", "") or ""

        return QueueMessage(
            message_kind="plan_result",
            sender="planner",
            trace_id=msg.trace_id,
            payload={
                "plan_markdown": plan_markdown,
                "message": message,
                "is_replan": True,
                "original_goal": original_goal,
            },
        )

    async def _handle_research_result(self, msg: QueueMessage) -> QueueMessage | None:
        """Feed research results back into the planner.

        Research results complete an in-flight research request. The planner
        may need to integrate this into an ongoing plan. For now, we re-run
        the planner with the research context appended.
        """
        research_data = str(msg.payload.get("result", ""))
        original_request = str(msg.payload.get("original_request", ""))

        prompt = (
            f"Research result received for request: {original_request}\n\n"
            f"Result:\n{research_data}\n\n"
            f"Integrate this into the current plan."
        )
        result = await self._planner.run(prompt)
        output = getattr(result, "output", None)

        plan_action = getattr(output, "plan_action", None) if output else None
        plan_markdown = getattr(plan_action, "plan_markdown", "") or "" if plan_action else ""
        message_text = getattr(output, "message", "") or "" if output else ""

        if plan_markdown:
            return QueueMessage(
                message_kind="plan_result",
                sender="planner",
                trace_id=msg.trace_id,
                payload={"plan_markdown": plan_markdown, "message": message_text},
            )
        return None


class ExecutorConsumer(BaseConsumer):
    """Consumes from executor_queue. Dispatches to ExecutorAgent.

    Handles: execution_request, research_request.

    For execution_request: runs executor in full mode → enqueues execution_status.
    For research_request: runs executor in research (read-only) mode → enqueues research_result.
    """

    def __init__(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        executor_agent: ExecutorAgentProtocol,
        *,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        super().__init__(store, router, "executor_queue", max_attempts=max_attempts)
        self._executor = executor_agent

    async def _process(self, msg: QueueMessage) -> QueueMessage | None:
        """Dispatch executor messages and produce status/result responses."""
        kind = msg.message_kind

        if kind == "execution_request":
            return await self._handle_execution_request(msg)
        if kind == "research_request":
            return await self._handle_research_request(msg)

        logger.warning("ExecutorConsumer received unexpected kind: %s", kind)
        return None

    async def _handle_execution_request(self, msg: QueueMessage) -> QueueMessage:
        """Run executor on a work item and produce an execution_status."""
        prompt = str(msg.payload.get("task_description", msg.payload.get("body", "")))
        work_item_id = str(msg.payload.get("work_item_id", ""))

        result = await self._executor.run(prompt)
        output = getattr(result, "output", None)

        summary = getattr(output, "summary", "Execution completed.") if output else "Execution completed."
        last_error = getattr(output, "last_error", None) if output else None
        status = "failed" if last_error else "done"

        return QueueMessage(
            message_kind="execution_status",
            sender="executor",
            trace_id=msg.trace_id,
            payload={
                "status": status,
                "work_item_id": work_item_id,
                "summary": summary,
                "last_error": last_error,
            },
        )

    async def _handle_research_request(self, msg: QueueMessage) -> QueueMessage:
        """Run executor in research mode (read-only) and produce research_result."""
        query = str(msg.payload.get("query", msg.payload.get("research_query", "")))
        original_request = str(msg.payload.get("original_request", ""))

        # Why prepend "RESEARCH MODE": signals to the executor agent that
        # it should only use read-only tools, even if write tools are available.
        prompt = f"RESEARCH MODE (read-only):\n{query}"
        result = await self._executor.run(prompt)
        output = getattr(result, "output", None)

        summary = getattr(output, "summary", "") if output else ""

        return QueueMessage(
            message_kind="research_result",
            sender="executor",
            trace_id=msg.trace_id,
            payload={
                "result": summary,
                "original_request": original_request,
                "query": query,
            },
        )


__all__ = [
    "BaseConsumer",
    "ExecutorConsumer",
    "PlannerConsumer",
    "ProxyConsumer",
]
