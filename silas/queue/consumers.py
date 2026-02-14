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

import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path
from typing import Protocol, runtime_checkable

from silas.core.plan_parser import MarkdownPlanParser
from silas.models.approval import ApprovalVerdict
from silas.models.work import WorkItem, WorkItemResult
from silas.protocols.work import WorkItemExecutor
from silas.queue.consult import ConsultPlannerManager
from silas.queue.replan import ReplanManager
from silas.queue.research import ResearchStateMachine
from silas.queue.router import QueueRouter
from silas.queue.status_router import route_to_surface
from silas.queue.store import DurableQueueStore
from silas.queue.types import (
    ExecutionRequestPayload,
    PlanRequestPayload,
    QueueMessage,
    StatusPayload,
    UserMessagePayload,
)
from silas.tools.backends import build_research_console_toolset
from silas.tools.filtered import FilteredToolset
from silas.work.executor import work_item_from_execution_payload

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
            await self._store.dead_letter(msg.id, f"max_attempts_exceeded ({self._max_attempts})")
            logger.warning(
                "Dead-lettered message %s after %d attempts",
                msg.id,
                msg.attempt_count,
            )
            return True

        heartbeat_task = asyncio.create_task(self._heartbeat_lease(msg.id))
        try:
            response = await self._process(msg)
            await self._store.mark_processed(self._consumer_name, msg.id)
            await self._store.ack(msg.id)

            # Route the response message onward if the processor produced one.
            if response is not None:
                await self._router.route(response)
            return True

        except Exception:
            logger.exception(
                "Consumer %s failed processing message %s", self._consumer_name, msg.id
            )
            await self._store.nack(msg.id)
            return True
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task

    async def _heartbeat_lease(self, message_id: str) -> None:
        """Periodically extend the active lease while message processing runs."""
        while True:
            await asyncio.sleep(20)
            await self._store.heartbeat(message_id)

    async def _process(self, msg: QueueMessage) -> QueueMessage | None:
        """Subclasses implement this. Returns a response message to route, or None."""
        raise NotImplementedError

    @staticmethod
    def _allowlist_from_metadata(
        metadata: dict[str, object] | None,
        key: str,
    ) -> list[str]:
        if not isinstance(metadata, dict):
            return []
        raw_allowlist = metadata.get(key)
        if not isinstance(raw_allowlist, list):
            return []
        return [str(tool_name) for tool_name in raw_allowlist if isinstance(tool_name, str)]

    async def _run_agent_with_allowlist(
        self,
        agent: ProxyAgentProtocol | PlannerAgentProtocol | ExecutorAgentProtocol,
        prompt: str,
        tool_allowlist: list[str],
    ) -> object:
        if not tool_allowlist:
            return await agent.run(prompt)

        original_toolset = getattr(agent, "toolset", None)
        has_toolset_api = (
            original_toolset is not None
            and callable(getattr(original_toolset, "list_tools", None))
            and callable(getattr(original_toolset, "call", None))
        )
        if not has_toolset_api:
            return await agent.run(prompt)

        agent.toolset = FilteredToolset(original_toolset, tool_allowlist)  # type: ignore[attr-defined]
        try:
            return await agent.run(prompt)
        finally:
            agent.toolset = original_toolset  # type: ignore[attr-defined]


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
        channel: object | None = None,
        approval_recipient_id: str = "owner",
        *,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        super().__init__(store, router, "proxy_queue", max_attempts=max_attempts)
        self._proxy = proxy_agent
        self._channel = channel
        self._approval_recipient_id = approval_recipient_id
        self._plan_parser = MarkdownPlanParser()

    async def _process(self, msg: QueueMessage) -> QueueMessage | None:
        """Dispatch based on message_kind and produce response messages."""
        kind = msg.message_kind

        if kind == "user_message":
            return await self._handle_user_message(msg)
        if kind == "execution_status":
            return self._handle_execution_status(msg)
        if kind == "plan_result":
            return await self._handle_plan_result(msg)
        if kind == "agent_response":
            # agent_response is terminal for QueueBridge.collect_response.
            # Re-processing it through the proxy creates feedback loops.
            return None

        # For approval_request/system_event: run proxy and return whatever it says.
        # These are informational messages that the proxy surfaces to the user.
        return await self._handle_generic(msg)

    async def _handle_user_message(self, msg: QueueMessage) -> QueueMessage | None:
        """Run proxy agent on user message. Route to planner if needed."""
        typed_payload = msg.typed_payload()
        user_payload = typed_payload if isinstance(typed_payload, UserMessagePayload) else None
        user_text = (
            user_payload.text if user_payload is not None else str(msg.payload.get("text", ""))
        )
        metadata = (
            user_payload.metadata if user_payload is not None else msg.payload.get("metadata")
        )
        rendered_context = ""
        personality_directives = ""
        if isinstance(metadata, dict):
            rendered_context = self._deserialize_rendered_context(metadata)
            personality_directives = str(metadata.get("personality_directives", ""))

        # Include chronicle context so the proxy has conversation history.
        prompt = f"{rendered_context}\n\nUser: {user_text}" if rendered_context else user_text
        if personality_directives:
            prompt = f"{personality_directives}\n\n{prompt}"
        result = await self._run_agent_with_allowlist(
            self._proxy,
            prompt,
            msg.tool_allowlist,
        )

        # Why getattr: ProxyRunResult has .output.route, but we use a
        # protocol so we access it generically to stay decoupled.
        output = getattr(result, "output", None)
        route = getattr(output, "route", "direct")

        if route == "planner":
            planner_allowlist = self._allowlist_from_metadata(
                metadata,
                "planner_tool_allowlist",
            )
            return QueueMessage(
                message_kind="plan_request",
                sender="proxy",
                trace_id=msg.trace_id,
                payload={
                    "user_request": prompt,
                    "reason": getattr(output, "reason", ""),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                },
                tool_allowlist=planner_allowlist,
            )

        # Direct response: send agent_response back so collect_response picks it up.
        agent_response = getattr(output, "response", None)
        response_text = getattr(agent_response, "message", "")
        if not response_text:
            response_text = getattr(output, "message", "")

        memory_queries: list[object] = []
        raw_queries = getattr(agent_response, "memory_queries", None)
        if isinstance(raw_queries, list):
            memory_queries = [
                query.model_dump(mode="json") if hasattr(query, "model_dump") else query
                for query in raw_queries
            ]

        memory_ops: list[object] = []
        raw_ops = getattr(agent_response, "memory_ops", None)
        if isinstance(raw_ops, list):
            memory_ops = [
                op.model_dump(mode="json") if hasattr(op, "model_dump") else op for op in raw_ops
            ]
        return QueueMessage(
            message_kind="agent_response",
            sender="proxy",
            trace_id=msg.trace_id,
            payload={
                "text": str(response_text or ""),
                "agent_response": (
                    agent_response.model_dump(mode="json")
                    if hasattr(agent_response, "model_dump")
                    else None
                ),
                "memory_queries": memory_queries,
                "memory_ops": memory_ops,
            },
            tool_allowlist=msg.tool_allowlist,
        )

    @staticmethod
    def _deserialize_rendered_context(metadata: dict[str, object]) -> str:
        """Extract rendered context from JSON metadata, falling back to legacy text."""
        serialized = metadata.get("rendered_context_json")
        if isinstance(serialized, str) and serialized.strip():
            try:
                parsed = json.loads(serialized)
            except json.JSONDecodeError:
                return ""
            if isinstance(parsed, dict):
                raw = parsed.get("rendered_context", "")
                return str(raw)
        return str(metadata.get("rendered_context", ""))

    def _handle_execution_status(self, msg: QueueMessage) -> QueueMessage | None:
        """Route execution status to appropriate UI surfaces.

        Why no async: status routing is a pure data transform with no I/O.
        The actual UI notification happens downstream.
        """
        typed_payload = msg.typed_payload()
        status_payload = typed_payload if isinstance(typed_payload, StatusPayload) else None
        status_str = (
            status_payload.status.value
            if status_payload is not None
            else str(msg.payload.get("status", ""))
        )
        surfaces = route_to_surface(status_str)

        # Attach surface routing info to the payload so downstream consumers
        # know where to deliver the notification.
        enriched_payload = dict(msg.payload)
        enriched_payload["surfaces"] = list(surfaces)

        # Status messages terminate here — they're informational, not routable
        # to another agent queue.
        return None

    async def _handle_plan_result(self, msg: QueueMessage) -> QueueMessage | None:
        """Parse plan result, request approval, then dispatch execution work.

        Queue path approval parity: planner-produced plans must be approved
        before they become execution_request messages.
        """
        plan_markdown = str(msg.payload.get("plan_markdown", ""))
        if not plan_markdown.strip():
            return None

        try:
            work_item = self._plan_parser.parse(plan_markdown)
        except ValueError:
            logger.warning("Ignoring invalid planner markdown for trace_id=%s", msg.trace_id)
            return None

        approved = await self._request_plan_approval(work_item)
        if not approved:
            return None

        metadata = msg.payload.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        executor_allowlist = self._allowlist_from_metadata(
            metadata_dict,
            "executor_tool_allowlist",
        )
        return QueueMessage(
            message_kind="execution_request",
            sender="proxy",
            trace_id=msg.trace_id,
            payload={
                "work_item_id": work_item.id,
                "task_description": work_item.body,
                "title": work_item.title,
                "metadata": metadata_dict,
            },
            work_item_id=work_item.id,
            tool_allowlist=executor_allowlist,
        )

    async def _request_plan_approval(self, work_item: WorkItem) -> bool:
        channel = self._channel
        if channel is None:
            return False

        send_approval_request = getattr(channel, "send_approval_request", None)
        if not callable(send_approval_request):
            return False

        try:
            decision = await send_approval_request(self._approval_recipient_id, work_item)
        except (OSError, RuntimeError, ValueError, TypeError):
            logger.exception("Plan approval request failed for work_item=%s", work_item.id)
            return False

        verdict = getattr(decision, "verdict", None)
        return verdict == ApprovalVerdict.approved

    async def _handle_generic(self, msg: QueueMessage) -> QueueMessage | None:
        """Run proxy on informational messages (agent_response, system_event, etc.)."""
        prompt = str(msg.payload.get("text", msg.payload.get("message", "")))
        await self._run_agent_with_allowlist(self._proxy, prompt, msg.tool_allowlist)
        return None


class PlannerConsumer(BaseConsumer):
    """Consumes from planner_queue. Dispatches to PlannerAgent.

    Handles: plan_request, research_result, replan_request.

    Uses ResearchStateMachine (§4.8) to track multi-step research flows:
    planner runs → requests research → executor responds → planner finalizes.
    """

    def __init__(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        planner_agent: PlannerAgentProtocol,
        *,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        research_sm: ResearchStateMachine | None = None,
    ) -> None:
        super().__init__(store, router, "planner_queue", max_attempts=max_attempts)
        self._planner = planner_agent
        # Why injectable: tests can provide a pre-configured state machine
        # with custom caps/timeouts without monkey-patching.
        self._research = research_sm or ResearchStateMachine()

    @property
    def research_sm(self) -> ResearchStateMachine:
        """Expose state machine for testing and introspection."""
        return self._research

    async def _process(self, msg: QueueMessage) -> QueueMessage | None:
        """Dispatch planner messages and produce plan_result responses."""
        kind = msg.message_kind

        if kind == "plan_request":
            return await self._handle_plan_request(msg)
        if kind == "replan_request":
            return await self._handle_replan_request(msg)
        if kind == "research_result":
            return await self._handle_research_result(msg)

        logger.warning("PlannerConsumer received unexpected kind: %s", kind)
        return None

    async def _handle_plan_request(self, msg: QueueMessage) -> QueueMessage | None:
        """Run planner and check if it requests research before finalizing.

        Why nullable return: if the planner requests research, we dispatch
        research_request messages via the router but don't produce a
        plan_result yet — that comes when research completes.
        """
        self._research.reset()
        typed_payload = msg.typed_payload()
        plan_payload = typed_payload if isinstance(typed_payload, PlanRequestPayload) else None
        user_request = (
            plan_payload.user_request
            if plan_payload is not None
            else str(msg.payload.get("user_request", ""))
        )
        result = await self._run_agent_with_allowlist(
            self._planner,
            user_request,
            msg.tool_allowlist,
        )
        output = getattr(result, "output", None)

        # Check if planner's response includes research requests
        research_requests = self._extract_research_requests(output)

        if research_requests:
            return await self._dispatch_research(research_requests, msg)

        return self._build_plan_result(output, msg, user_request)

    def _extract_research_requests(self, output: object) -> list[dict[str, object]]:
        """Pull research requests from planner output if present.

        Why getattr chain: planner output is accessed via protocol, so we
        can't assume concrete types. Research requests are an optional list
        on the output object.
        """
        if output is None:
            return []
        requests = getattr(output, "research_requests", None)
        if isinstance(requests, list):
            return [r if isinstance(r, dict) else vars(r) for r in requests]
        return []

    async def _dispatch_research(
        self, requests: list[dict[str, object]], origin_msg: QueueMessage
    ) -> QueueMessage | None:
        """Enqueue research requests to executor_queue via state machine.

        Returns None because plan_result comes later when research completes.
        """
        for req in requests:
            request_id = str(req.get("request_id", ""))
            query = str(req.get("query", ""))
            return_format = str(req.get("return_format", ""))
            max_tokens = int(req.get("max_tokens", 500))

            metadata = origin_msg.payload.get("metadata")
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            executor_allowlist = self._allowlist_from_metadata(
                metadata_dict,
                "executor_tool_allowlist",
            )
            accepted = self._research.request_research(
                request_id=request_id,
                query=query,
                return_format=return_format,
                max_tokens=max_tokens,
            )

            if accepted:
                research_msg = QueueMessage(
                    message_kind="research_request",
                    sender="planner",
                    trace_id=origin_msg.trace_id,
                    payload={
                        "request_id": request_id,
                        "query": query,
                        "return_format": return_format,
                        "max_tokens": max_tokens,
                        "original_request": str(origin_msg.payload.get("user_request", "")),
                        "research_mode": True,
                        "metadata": metadata_dict,
                    },
                    tool_allowlist=executor_allowlist,
                )
                await self._router.route(research_msg)

        return None

    async def _handle_replan_request(self, msg: QueueMessage) -> QueueMessage:
        """Run planner with failure context to produce a revised plan."""
        original_goal = str(msg.payload.get("original_goal", ""))
        failure_history = msg.payload.get("failure_history", [])
        prompt = (
            f"REPLAN REQUEST — previous approach failed.\n\n"
            f"Original goal: {original_goal}\n\n"
            f"Failure history:\n{failure_history}\n\n"
            f"Generate an alternative strategy. Do NOT retry the same approach."
        )
        result = await self._run_agent_with_allowlist(
            self._planner,
            prompt,
            msg.tool_allowlist,
        )
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
            tool_allowlist=msg.tool_allowlist,
        )

    async def _handle_research_result(self, msg: QueueMessage) -> QueueMessage | None:
        """Feed research result into state machine; finalize if all results are in.

        Why check state: if the SM is already expired or finalized, late
        results are ignored per §4.8 cancel semantics.
        """
        request_id = str(msg.payload.get("request_id", ""))
        research_data = str(msg.payload.get("result", ""))

        accepted = self._research.receive_result(
            request_id=request_id,
            result=research_data,
            message_id=msg.id,
        )

        if not accepted:
            return None

        # Check timeouts on remaining in-flight requests
        self._research.check_timeouts()

        from silas.queue.research import ResearchState

        if self._research.state in (
            ResearchState.ready_to_finalize,
            ResearchState.expired,
        ):
            return await self._finalize_with_research(msg)

        # Still waiting for more results
        return None

    async def _finalize_with_research(self, msg: QueueMessage) -> QueueMessage:
        """Re-run planner with collected research results to produce final plan."""
        from silas.queue.research import ResearchState

        is_expired = self._research.state == ResearchState.expired
        results = self._research.finalize()
        is_partial = is_expired or self._research.last_finalize_was_partial

        # Build research context for the planner
        research_context = "\n\n".join(
            f"[Research {rid}]:\n{text}" for rid, text in results.items()
        )

        prompt = (
            f"Finalize plan with research results"
            f"{' (PARTIAL — some research timed out)' if is_partial else ''}:\n\n"
            f"{research_context}\n\n"
            f"Produce the final plan now."
        )

        result = await self._run_agent_with_allowlist(
            self._planner,
            prompt,
            msg.tool_allowlist,
        )
        output = getattr(result, "output", None)

        plan_action = getattr(output, "plan_action", None) if output else None
        plan_markdown = getattr(plan_action, "plan_markdown", "") or "" if plan_action else ""
        message_text = getattr(output, "message", "") or "" if output else ""

        metadata = msg.payload.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        return QueueMessage(
            message_kind="plan_result",
            sender="planner",
            trace_id=msg.trace_id,
            payload={
                "plan_markdown": plan_markdown,
                "message": message_text,
                "partial_research": is_partial,
                "metadata": metadata_dict,
            },
            tool_allowlist=self._allowlist_from_metadata(metadata_dict, "proxy_tool_allowlist"),
        )

    def _build_plan_result(
        self,
        output: object,
        msg: QueueMessage,
        user_request: str,
    ) -> QueueMessage:
        """Extract plan from planner output and build plan_result message."""
        plan_markdown = ""
        message = ""
        if output is not None:
            plan_action = getattr(output, "plan_action", None)
            if plan_action is not None:
                plan_markdown = getattr(plan_action, "plan_markdown", "") or ""
            message = getattr(output, "message", "") or ""

        metadata = msg.payload.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        return QueueMessage(
            message_kind="plan_result",
            sender="planner",
            trace_id=msg.trace_id,
            payload={
                "plan_markdown": plan_markdown,
                "message": message,
                "user_request": user_request,
                "metadata": metadata_dict,
            },
            tool_allowlist=self._allowlist_from_metadata(metadata_dict, "proxy_tool_allowlist"),
        )


class ExecutorConsumer(BaseConsumer):
    """Consumes from executor_queue. Dispatches to ExecutorAgent.

    Handles: execution_request, research_request.

    For execution_request: runs executor with the full self-healing cascade
    (Principle #8): retry → consult-planner → replan → escalate.
    For research_request: runs executor in research (read-only) mode → enqueues research_result.
    """

    def __init__(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        executor_agent: ExecutorAgentProtocol,
        *,
        work_executor: WorkItemExecutor | None = None,
        consult_manager: ConsultPlannerManager | None = None,
        replan_manager: ReplanManager | None = None,
        workspace_path: Path | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        super().__init__(store, router, "executor_queue", max_attempts=max_attempts)
        self._executor = executor_agent
        self._work_executor = work_executor
        # Why optional: existing callers (tests, simple deployments) that don't
        # need the cascade can omit these. When absent, failures fall through
        # directly to execution_status with status=failed.
        self._consult = consult_manager
        self._replan = replan_manager
        self._workspace_path = workspace_path or Path.cwd()

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
        """Run executor with self-healing cascade (Principle #8).

        Flow: execute → if failed and on_stuck=consult_planner → consult
        planner for guidance → retry with guidance → if still fails →
        trigger replan (up to max_replan_depth=2) → escalate to user.

        Budget attribution: executor tokens charge to work-item budget,
        consult/replan tokens charge to plan budget (handled by
        ConsultPlannerManager routing through planner_queue).
        """
        # Prefer the work-item executor path when a serialized work item
        # is provided. This enables planner->executor queue dispatch to run
        # the same LiveWorkItemExecutor pipeline as procedural execution.
        if self._work_executor is not None:
            work_item = self._deserialize_work_item(msg)
            if work_item is not None:
                result = await self._work_executor.execute(work_item)
                return self._status_from_work_result(msg, work_item.id, result)

        typed_payload = msg.typed_payload()
        execution_payload = (
            typed_payload if isinstance(typed_payload, ExecutionRequestPayload) else None
        )
        prompt = (
            execution_payload.task_description or execution_payload.body
            if execution_payload is not None
            else str(msg.payload.get("task_description", msg.payload.get("body", "")))
        )
        # Why prefer first-class field: work_item_id on the envelope is the
        # spec-mandated location (§2.1). Fall back to payload for backward compat.
        work_item_id = msg.work_item_id or (
            execution_payload.work_item_id
            if execution_payload is not None
            else str(msg.payload.get("work_item_id", ""))
        )
        on_stuck = str(msg.payload.get("on_stuck", "consult_planner"))
        original_goal = str(msg.payload.get("original_goal", prompt))
        replan_depth = int(msg.payload.get("replan_depth", 0))

        result = await self._run_agent_with_allowlist(
            self._executor,
            prompt,
            msg.tool_allowlist,
        )
        output = getattr(result, "output", None)

        summary = (
            getattr(output, "summary", "Execution completed.") if output else "Execution completed."
        )
        last_error = getattr(output, "last_error", None) if output else None
        status = "failed" if last_error else "done"

        # Happy path: execution succeeded, no cascade needed.
        if status == "done":
            return self._build_status_msg(msg.trace_id, work_item_id, "done", summary)

        # Self-healing cascade (Principle #8): consult → retry → replan → escalate.
        # Only triggers when on_stuck requests it AND the managers are wired in.
        if on_stuck == "consult_planner" and self._consult is not None:
            guidance = await self._consult.consult(
                work_item_id=work_item_id,
                failure_context=f"Execution failed: {last_error}\n\nOriginal task: {prompt}",
                trace_id=msg.trace_id,
            )

            if guidance is not None:
                # Retry once with planner guidance appended to the prompt.
                # Guidance tokens were charged to plan budget (routed through
                # planner_queue by ConsultPlannerManager).
                guided_prompt = f"{prompt}\n\n## Planner Guidance\n{guidance}"
                retry_result = await self._run_agent_with_allowlist(
                    self._executor,
                    guided_prompt,
                    msg.tool_allowlist,
                )
                retry_output = getattr(retry_result, "output", None)
                retry_error = getattr(retry_output, "last_error", None) if retry_output else None
                retry_summary = (
                    getattr(retry_output, "summary", "Execution completed.")
                    if retry_output
                    else "Execution completed."
                )

                if not retry_error:
                    return self._build_status_msg(
                        msg.trace_id,
                        work_item_id,
                        "done",
                        retry_summary,
                    )
                # Guided retry also failed — update context for replan.
                last_error = retry_error
                summary = retry_summary

            # Consult exhausted (timed out or guided retry failed) → trigger replan.
            if self._replan is not None:
                failure_history: list[dict[str, object]] = [
                    {"phase": "execution", "error": str(last_error)},
                    {
                        "phase": "consult",
                        "result": "timeout" if guidance is None else "guidance_failed",
                    },
                ]
                replan_enqueued = await self._replan.trigger_replan(
                    work_item_id=work_item_id,
                    original_goal=original_goal,
                    failure_history=failure_history,
                    trace_id=msg.trace_id,
                    current_depth=replan_depth,
                )

                if replan_enqueued:
                    # Replan was sent to planner — report stuck (not failed)
                    # so the orchestrator knows recovery is in progress.
                    return self._build_status_msg(
                        msg.trace_id,
                        work_item_id,
                        "stuck",
                        f"Replan triggered (depth {replan_depth + 1}): {last_error}",
                    )

                # max_replan_depth exceeded → escalate to user.
                return self._build_status_msg(
                    msg.trace_id,
                    work_item_id,
                    "failed",
                    f"All recovery exhausted (replan depth {replan_depth}). "
                    f"Escalating to user. Last error: {last_error}",
                    escalated=True,
                )

        # No cascade available or on_stuck doesn't request it.
        return self._build_status_msg(
            msg.trace_id,
            work_item_id,
            "failed",
            summary,
            last_error=last_error,
        )

    def _deserialize_work_item(self, msg: QueueMessage) -> WorkItem | None:
        """Return a validated WorkItem from execution payload or None when absent.

        Raises:
            ValueError: Payload looks like a work item but fails validation.
        """
        return work_item_from_execution_payload(msg.payload)

    def _status_from_work_result(
        self,
        msg: QueueMessage,
        work_item_id: str,
        result: WorkItemResult,
    ) -> QueueMessage:
        """Translate WorkItemResult into the queue status message contract."""
        last_error = result.last_error
        return self._build_status_msg(
            msg.trace_id,
            work_item_id,
            result.status.value,
            result.summary,
            last_error=last_error,
        )

    def _build_status_msg(
        self,
        trace_id: str,
        work_item_id: str,
        status: str,
        summary: str,
        *,
        last_error: str | None = None,
        escalated: bool = False,
    ) -> QueueMessage:
        """Build an execution_status message. Factored out to reduce duplication."""
        payload: dict[str, object] = {
            "status": status,
            "work_item_id": work_item_id,
            "summary": summary,
        }
        if last_error is not None:
            payload["last_error"] = last_error
        if escalated:
            payload["escalated"] = True
        return QueueMessage(
            message_kind="execution_status",
            sender="executor",
            trace_id=trace_id,
            payload=payload,
        )

    async def _handle_research_request(self, msg: QueueMessage) -> QueueMessage:
        """Run executor in research mode (read-only) and produce research_result."""
        query = str(msg.payload.get("query", msg.payload.get("research_query", "")))
        original_request = str(msg.payload.get("original_request", ""))
        research_console_toolset = build_research_console_toolset(self._workspace_path)
        research_allowlist = [str(tool_name) for tool_name in research_console_toolset.tools]

        # Why prepend "RESEARCH MODE": signals to the executor agent that
        # it should only use read-only tools, even if write tools are available.
        prompt = f"RESEARCH MODE (read-only):\n{query}"
        result = await self._run_agent_with_allowlist(
            self._executor,
            prompt,
            research_allowlist,
        )
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
