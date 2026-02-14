"""End-to-end queue loop integration tests.

Runs messages through the full queue pipeline with real DurableQueueStore
(in-memory SQLite), real QueueRouter, and real QueueOrchestrator. Only the
AI model calls are mocked — everything else is production code.

Why these tests exist: the queue-based execution path is the default, but
prior to this file there was no test covering the complete message lifecycle
from dispatch_turn through all consumers back to collect_response.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from silas.queue.bridge import QueueBridge
from silas.queue.consumers import ExecutorConsumer, PlannerConsumer, ProxyConsumer
from silas.queue.orchestrator import QueueOrchestrator
from silas.queue.router import QueueRouter
from silas.queue.store import DurableQueueStore
from silas.queue.types import QueueMessage
from tests.helpers import wait_until

# ── Mock Agent Outputs ─────────────────────────────────────────────────
# Why dataclasses instead of SimpleNamespace: consumers access attributes
# via getattr chains (result.output.route), and dataclasses give us
# predictable attribute access without surprises from missing fields.


@dataclass
class _PlanAction:
    plan_markdown: str = "## Step 1\nDo the thing"


@dataclass
class _MockOutput:
    route: str = "direct"
    reason: str = ""
    message: str = "Mock response"
    summary: str = "Mock summary"
    last_error: str | None = None
    plan_action: _PlanAction | None = None
    research_requests: list[object] | None = None


@dataclass
class _MockResult:
    output: _MockOutput


# ── Mock Agents ────────────────────────────────────────────────────────


class DirectProxyAgent:
    """Mock proxy that routes direct and side-effects an agent_response.

    Why the side-effect: ProxyConsumer._handle_user_message returns None
    for the "direct" route (no further queue routing). In the real system,
    the proxy would send the response to the user via the channel layer.
    Here we simulate that by enqueuing an agent_response so
    bridge.collect_response() can pick it up — testing the full loop.
    """

    def __init__(self, router: QueueRouter) -> None:
        self._router = router
        self.call_count: int = 0
        self.last_prompt: str = ""

    async def run(self, prompt: str, deps: object | None = None) -> _MockResult:
        self.call_count += 1
        self.last_prompt = prompt
        # Side-effect: enqueue the agent_response that the bridge polls for.
        # We extract trace_id from a test-injected attribute set before dispatch.
        response = QueueMessage(
            message_kind="agent_response",
            sender="proxy",
            trace_id=self._current_trace_id,
            payload={"text": f"Direct answer to: {prompt}"},
        )
        await self._router.route(response)
        return _MockResult(output=_MockOutput(route="direct"))

    def set_trace_id(self, trace_id: str) -> None:
        """Inject the trace_id so the mock can tag its response correctly."""
        self._current_trace_id = trace_id


class PlannerRouteProxyAgent:
    """Mock proxy that routes to planner.

    The proxy consumer will create a plan_request message from the return value.
    """

    def __init__(self) -> None:
        self.call_count: int = 0

    async def run(self, prompt: str, deps: object | None = None) -> _MockResult:
        self.call_count += 1
        return _MockResult(output=_MockOutput(route="planner", reason="complex task"))


@dataclass
class PlannerAgentWithWorkItem:
    """Mock planner that produces a plan with work items.

    On first call (plan_request): returns a plan and side-effects an
    execution_request into executor_queue. On second call (plan_result
    comes back): produces agent_response for the bridge to collect.

    Why side-effect the execution_request: the real planner→executor handoff
    goes through the approval/scheduling layer which is outside queue consumers.
    We simulate it directly so the executor consumer has work to do.
    """

    router: QueueRouter
    call_count: int = 0
    _trace_id: str = ""

    def set_trace_id(self, trace_id: str) -> None:
        self._trace_id = trace_id

    async def run(self, prompt: str, deps: object | None = None) -> _MockResult:
        self.call_count += 1

        if self.call_count == 1:
            # First call: produce plan and enqueue execution_request
            exec_msg = QueueMessage(
                message_kind="execution_request",
                sender="planner",
                trace_id=self._trace_id,
                payload={
                    "work_item_id": "wi-001",
                    "task_description": "Execute the plan step",
                    "on_stuck": "consult_planner",
                    "original_goal": prompt,
                },
                work_item_id="wi-001",
            )
            await self.router.route(exec_msg)
            return _MockResult(
                output=_MockOutput(
                    message=f"Plan for: {prompt}",
                    plan_action=_PlanAction(),
                ),
            )

        # Subsequent calls: produce a response for the bridge
        return _MockResult(output=_MockOutput(message=f"Plan complete: {prompt}"))


class SuccessExecutorAgent:
    """Mock executor that always succeeds."""

    def __init__(self) -> None:
        self.call_count: int = 0
        self.prompts: list[str] = []

    async def run(self, prompt: str, deps: object | None = None) -> _MockResult:
        self.call_count += 1
        self.prompts.append(prompt)
        return _MockResult(output=_MockOutput(summary=f"Done: {prompt}"))


@dataclass
class FailThenSucceedExecutorAgent:
    """Mock executor that fails on first call, succeeds on second.

    Used to test the consult-planner → retry flow (Principle #8).
    """

    call_count: int = 0
    prompts: list[str] = field(default_factory=list)

    async def run(self, prompt: str, deps: object | None = None) -> _MockResult:
        self.call_count += 1
        self.prompts.append(prompt)

        if self.call_count == 1:
            return _MockResult(
                output=_MockOutput(
                    summary="Failed to execute",
                    last_error="connection_timeout: API unreachable",
                ),
            )

        # Second call (with planner guidance) succeeds
        return _MockResult(
            output=_MockOutput(summary=f"Succeeded with guidance: {prompt}"),
        )


class ConcurrentProxyAgent:
    """Mock proxy for concurrent tests: tags responses with the input prompt.

    Why per-call trace tracking: in concurrent tests, multiple dispatch_turns
    happen simultaneously. The agent needs to produce agent_responses tagged
    with the correct trace_id for each call, not a single shared one.
    """

    def __init__(self, router: QueueRouter, store: DurableQueueStore) -> None:
        self._router = router
        self._store = store
        self.call_count: int = 0
        # Map prompt text → trace_id, set before dispatch
        self._prompt_to_trace: dict[str, str] = {}

    def register_trace(self, prompt: str, trace_id: str) -> None:
        self._prompt_to_trace[prompt] = trace_id

    async def run(self, prompt: str, deps: object | None = None) -> _MockResult:
        self.call_count += 1
        trace_id = self._prompt_to_trace.get(prompt, "unknown")
        response = QueueMessage(
            message_kind="agent_response",
            sender="proxy",
            trace_id=trace_id,
            payload={"text": f"Response to: {prompt}"},
        )
        await self._router.route(response)
        return _MockResult(output=_MockOutput(route="direct"))


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    return str(tmp_path / "e2e_queue.db")


@pytest.fixture
async def store(tmp_db: str) -> DurableQueueStore:
    s = DurableQueueStore(tmp_db)
    await s.initialize()
    return s


@pytest.fixture
def router(store: DurableQueueStore) -> QueueRouter:
    return QueueRouter(store)


# ── Test 1: Direct Route Full Loop ─────────────────────────────────────


class TestDirectRouteFullLoop:
    """user_message → proxy_queue → proxy consumer → agent_response → collected."""

    async def test_dispatch_through_collect(
        self, store: DurableQueueStore, router: QueueRouter,
    ) -> None:
        proxy = DirectProxyAgent(router)
        planner = PlannerAgentWithWorkItem(router=router)
        executor = SuccessExecutorAgent()

        proxy_consumer = ProxyConsumer(store, router, proxy)
        planner_consumer = PlannerConsumer(store, router, planner)
        executor_consumer = ExecutorConsumer(store, router, executor)

        orchestrator = QueueOrchestrator(
            store=store,
            router=router,
            consumers=[proxy_consumer, planner_consumer, executor_consumer],
            poll_interval_s=0.05,
        )
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)

        trace_id = str(uuid.uuid4())
        proxy.set_trace_id(trace_id)

        await bridge.dispatch_turn("What is 2+2?", trace_id=trace_id)
        await orchestrator.start()

        try:
            result = await bridge.collect_response(trace_id=trace_id, timeout_s=5.0)
        finally:
            await orchestrator.stop()

        # Core assertions: response arrived, trace_id preserved, correct content
        assert result is not None, "Bridge should have collected the agent_response"
        assert result.trace_id == trace_id
        assert result.message_kind == "agent_response"
        assert "What is 2+2?" in result.payload["text"]
        # Why >= 1: proxy processes the user_message (call 1), and may also
        # process the agent_response routed back to proxy_queue (call 2).
        assert proxy.call_count >= 1

    async def test_trace_id_propagation_in_audit(
        self, store: DurableQueueStore, router: QueueRouter,
    ) -> None:
        """Verify the processed_messages table records the proxy consumer's work."""
        proxy = DirectProxyAgent(router)
        proxy_consumer = ProxyConsumer(store, router, proxy)
        orchestrator = QueueOrchestrator(
            store=store, router=router, consumers=[proxy_consumer], poll_interval_s=0.05,
        )
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)

        trace_id = str(uuid.uuid4())
        proxy.set_trace_id(trace_id)

        await bridge.dispatch_turn("audit test", trace_id=trace_id)
        await orchestrator.start()
        await wait_until(lambda: proxy.call_count >= 1, timeout=3.0)
        await orchestrator.stop()

        # The proxy consumer should have marked the user_message as processed.
        # Why check has_processed: confirms the idempotency guard (§2.2.1) works.
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM processed_messages WHERE consumer = ?",
                ("consumer:proxy_queue",),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] >= 1, "Proxy consumer should have recorded processed message"


# ── Test 2: Planner Route Full Loop ────────────────────────────────────


class TestPlannerRouteFullLoop:
    """user_message → proxy → plan_request → planner → execution_request → executor → status."""

    async def test_full_planner_executor_flow(
        self, store: DurableQueueStore, router: QueueRouter,
    ) -> None:
        proxy = PlannerRouteProxyAgent()
        planner = PlannerAgentWithWorkItem(router=router)
        executor = SuccessExecutorAgent()

        trace_id = str(uuid.uuid4())
        planner.set_trace_id(trace_id)

        proxy_consumer = ProxyConsumer(store, router, proxy)
        planner_consumer = PlannerConsumer(store, router, planner)
        executor_consumer = ExecutorConsumer(store, router, executor)

        orchestrator = QueueOrchestrator(
            store=store,
            router=router,
            consumers=[proxy_consumer, planner_consumer, executor_consumer],
            poll_interval_s=0.05,
        )
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)

        await bridge.dispatch_turn("Refactor auth module", trace_id=trace_id)
        await orchestrator.start()

        await wait_until(
            lambda: (
                proxy.call_count >= 1
                and planner.call_count >= 1
                and executor.call_count >= 1
            ),
            timeout=3.0,
        )
        await orchestrator.stop()

        # Verify the full chain executed
        assert proxy.call_count >= 1, "Proxy should have processed the user_message"
        assert planner.call_count >= 1, "Planner should have received plan_request"
        assert executor.call_count >= 1, "Executor should have received execution_request"

        # Verify execution_status flowed back to proxy_queue.
        # Why check executor prompts: confirms the execution_request payload
        # was correctly propagated from planner's side-effect.
        assert any("Execute the plan step" in p for p in executor.prompts)

    async def test_plan_and_execution_status_reach_proxy_queue(
        self, store: DurableQueueStore, router: QueueRouter,
    ) -> None:
        """Plan result and execution_status both route back to proxy_queue."""
        proxy = PlannerRouteProxyAgent()
        planner = PlannerAgentWithWorkItem(router=router)
        executor = SuccessExecutorAgent()

        trace_id = str(uuid.uuid4())
        planner.set_trace_id(trace_id)

        proxy_consumer = ProxyConsumer(store, router, proxy)
        planner_consumer = PlannerConsumer(store, router, planner)
        executor_consumer = ExecutorConsumer(store, router, executor)

        orchestrator = QueueOrchestrator(
            store=store,
            router=router,
            consumers=[proxy_consumer, planner_consumer, executor_consumer],
            poll_interval_s=0.05,
        )

        await router.route(QueueMessage(
            message_kind="user_message",
            sender="user",
            trace_id=trace_id,
            payload={"text": "Build a dashboard"},
        ))

        await orchestrator.start()
        await wait_until(
            lambda: (
                proxy.call_count >= 1
                and planner.call_count >= 1
                and executor.call_count >= 1
            ),
            timeout=5.0,
        )
        # Extra settle time for return messages to be processed
        import asyncio as _asyncio
        await _asyncio.sleep(0.5)
        await orchestrator.stop()

        # Verify the full chain ran: proxy processed user_message,
        # planner created plan + execution_request, executor ran it.
        assert proxy.call_count >= 1
        assert planner.call_count >= 1
        assert executor.call_count >= 1

        # Verify plan_result and execution_status were consumed from proxy_queue
        # (they should be ack'd/processed). Why check processed_messages: confirms
        # the consumer saw and handled the return messages, not just the initial one.
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM processed_messages WHERE consumer = ?",
                ("consumer:proxy_queue",),
            )
            row = await cursor.fetchone()
            assert row is not None
            # Why >= 2: proxy_queue consumer processes user_message + at least
            # plan_result or execution_status flowing back.
            assert row[0] >= 2, (
                f"Proxy consumer should process multiple messages, got {row[0]}"
            )


# ── Test 3: Failure Cascade ────────────────────────────────────────────


class TestFailureCascade:
    """Executor fails → consult-planner → retry with guidance → success."""

    async def test_consult_retry_succeeds(
        self, store: DurableQueueStore, router: QueueRouter,
    ) -> None:
        """Full failure cascade: fail → consult planner → guided retry → success."""
        from silas.queue.consult import ConsultPlannerManager

        executor = FailThenSucceedExecutorAgent()
        consult_mgr = ConsultPlannerManager(store=store, router=router)

        executor_consumer = ExecutorConsumer(
            store, router, executor, consult_manager=consult_mgr,
        )

        trace_id = str(uuid.uuid4())

        # Enqueue execution_request that the executor will pick up
        exec_msg = QueueMessage(
            message_kind="execution_request",
            sender="planner",
            trace_id=trace_id,
            payload={
                "work_item_id": "wi-fail-001",
                "task_description": "Deploy service to staging",
                "on_stuck": "consult_planner",
                "original_goal": "Deploy service",
            },
            work_item_id="wi-fail-001",
        )
        await router.route(exec_msg)

        # Simulate the planner's guidance response arriving in runtime_queue.
        # Why async task: ConsultPlannerManager.consult() polls runtime_queue
        # for a planner_guidance message. We need to enqueue it while the
        # consumer is blocked waiting.
        async def _inject_guidance_after_delay() -> None:
            # Wait for the consult request to be enqueued to planner_queue
            await asyncio.sleep(0.3)
            guidance_msg = QueueMessage(
                message_kind="planner_guidance",
                sender="planner",
                trace_id=trace_id,
                payload={
                    "guidance": "Use the backup API endpoint instead",
                    "work_item_id": "wi-fail-001",
                },
            )
            await router.route(guidance_msg)

        guidance_task = asyncio.create_task(_inject_guidance_after_delay())

        # Process the execution_request — this triggers:
        # 1. executor.run() → fails (first call)
        # 2. consult_mgr.consult() → sends plan_request, polls for guidance
        # 3. _inject_guidance_after_delay provides the guidance
        # 4. executor.run() with guidance → succeeds (second call)
        processed = await executor_consumer.poll_once()

        await guidance_task

        assert processed is True
        assert executor.call_count == 2, "Executor should run twice: fail then succeed"

        # The guided retry prompt should contain the planner's guidance
        assert any("backup API endpoint" in p for p in executor.prompts), (
            "Retry prompt should include planner guidance"
        )

        # The consult request should have gone to planner_queue
        # (already consumed by our guidance injection, but we can verify
        # the consult manager enqueued it by checking the planner_queue
        # processed_messages or the plan_request message kind)

    async def test_consult_timeout_produces_failure_status(
        self, store: DurableQueueStore, router: QueueRouter,
    ) -> None:
        """When consult times out, executor reports failure (no guidance arrives)."""
        from unittest.mock import AsyncMock

        from silas.queue.consult import ConsultPlannerManager

        executor = FailThenSucceedExecutorAgent()
        consult_mgr = ConsultPlannerManager(store=store, router=router)
        # Why mock consult to return None: simulates a timeout without
        # waiting the real 90s. The executor sees no guidance and skips retry.
        consult_mgr.consult = AsyncMock(return_value=None)  # type: ignore[method-assign]

        executor_consumer = ExecutorConsumer(
            store, router, executor, consult_manager=consult_mgr,
        )

        trace_id = str(uuid.uuid4())
        exec_msg = QueueMessage(
            message_kind="execution_request",
            sender="planner",
            trace_id=trace_id,
            payload={
                "work_item_id": "wi-timeout-001",
                "task_description": "Impossible task",
                "on_stuck": "consult_planner",
            },
            work_item_id="wi-timeout-001",
        )
        await router.route(exec_msg)

        processed = await executor_consumer.poll_once()

        assert processed is True
        # Only 1 call: executor failed, consult timed out (None), no retry
        assert executor.call_count == 1
        consult_mgr.consult.assert_called_once()


# ── Test 4: Concurrent Traces ──────────────────────────────────────────


class TestConcurrentTraces:
    """3 simultaneous dispatch_turns with different trace_ids — no cross-contamination."""

    async def test_three_parallel_traces(
        self, store: DurableQueueStore, router: QueueRouter,
    ) -> None:
        """3 concurrent dispatches with different trace_ids get correct responses.

        Why manual poll_once instead of orchestrator: the orchestrator's proxy
        consumer competes with bridge.collect_response for agent_response
        messages on proxy_queue. Using poll_once gives us deterministic
        processing order while still testing the real consumer code path.
        """
        proxy = ConcurrentProxyAgent(router, store)
        proxy_consumer = ProxyConsumer(store, router, proxy)

        # No orchestrator running — we'll drive processing manually
        orchestrator = QueueOrchestrator(
            store=store, router=router, consumers=[], poll_interval_s=0.05,
        )
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)

        traces: list[tuple[str, str]] = [
            (str(uuid.uuid4()), "What is Python?"),
            (str(uuid.uuid4()), "What is Rust?"),
            (str(uuid.uuid4()), "What is Go?"),
        ]

        for trace_id, prompt in traces:
            proxy.register_trace(prompt, trace_id)

        # Dispatch all 3
        for trace_id, prompt in traces:
            await bridge.dispatch_turn(prompt, trace_id=trace_id)

        # Process all 3 user_messages through proxy consumer (each produces
        # an agent_response side-effect). The agent_responses land in proxy_queue
        # but we DON'T let the consumer grab them — bridge.collect_response will.
        for _ in traces:
            await proxy_consumer.poll_once()

        # Now the proxy consumer also sees agent_responses in proxy_queue.
        # But collect_response uses lease_filtered (trace_id + message_kind),
        # which is atomic. We collect before the consumer can interfere.
        results = await asyncio.gather(
            *[
                bridge.collect_response(trace_id=tid, timeout_s=3.0)
                for tid, _ in traces
            ]
        )

        for i, (trace_id, prompt) in enumerate(traces):
            result = results[i]
            assert result is not None, f"Trace {trace_id} should have a response"
            assert result.trace_id == trace_id, (
                f"Response trace_id mismatch: expected {trace_id}, got {result.trace_id}"
            )
            assert prompt in result.payload["text"], (
                f"Response should contain original prompt '{prompt}', "
                f"got '{result.payload['text']}'"
            )

        assert proxy.call_count == 3, "Proxy should process exactly 3 user_messages"
