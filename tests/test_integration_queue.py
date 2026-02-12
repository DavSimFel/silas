"""Integration tests for the queue bridge, factory, and Stream integration.

Tests the full queue pipeline with mock agents and real SQLite storage.
Validates that the bridge correctly enqueues messages, the factory wires
components, and Stream's queue_bridge conditional works end-to-end.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from silas.queue.bridge import QueueBridge
from silas.queue.consumers import ProxyConsumer
from silas.queue.factory import create_queue_system
from silas.queue.orchestrator import QueueOrchestrator
from silas.queue.router import QueueRouter
from silas.queue.store import DurableQueueStore
from silas.queue.types import QueueMessage

# ── Mock Agents ────────────────────────────────────────────────────────


@dataclass
class _MockOutput:
    """Minimal mock output for agent.run() results."""

    route: str = "direct"
    reason: str = ""
    message: str = "Mock response"
    summary: str = "Mock summary"
    last_error: str | None = None
    plan_action: object | None = None


@dataclass
class _MockResult:
    """Wraps _MockOutput to match the result = agent.run() → result.output pattern."""

    output: _MockOutput


class MockProxyAgent:
    """Mock proxy that always routes direct."""

    def __init__(self, route: str = "direct") -> None:
        self._route = route
        self.call_count = 0

    async def run(self, prompt: str, deps: object | None = None) -> _MockResult:
        self.call_count += 1
        return _MockResult(output=_MockOutput(route=self._route))


class MockPlannerAgent:
    """Mock planner that produces a plan_result payload."""

    def __init__(self) -> None:
        self.call_count = 0

    async def run(self, prompt: str, deps: object | None = None) -> _MockResult:
        self.call_count += 1
        return _MockResult(
            output=_MockOutput(
                message=f"Plan for: {prompt}",
                plan_action=None,
            ),
        )


class MockExecutorAgent:
    """Mock executor that always succeeds."""

    def __init__(self) -> None:
        self.call_count = 0

    async def run(self, prompt: str, deps: object | None = None) -> _MockResult:
        self.call_count += 1
        return _MockResult(output=_MockOutput(summary=f"Executed: {prompt}"))


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Provide a temporary SQLite database path."""
    return str(tmp_path / "test_queue.db")


@pytest.fixture
async def store(tmp_db: str) -> DurableQueueStore:
    """Initialize and return a DurableQueueStore."""
    s = DurableQueueStore(tmp_db)
    await s.initialize()
    return s


@pytest.fixture
def router(store: DurableQueueStore) -> QueueRouter:
    return QueueRouter(store)


# ── QueueBridge Tests ──────────────────────────────────────────────────


class TestQueueBridgeDispatchTurn:
    """Verify dispatch_turn enqueues user_message to proxy_queue."""

    async def test_enqueues_user_message(self, store: DurableQueueStore, router: QueueRouter) -> None:
        orchestrator = QueueOrchestrator(store=store, router=router, consumers=[])
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)
        trace_id = str(uuid.uuid4())

        await bridge.dispatch_turn("hello", trace_id=trace_id)

        # Why lease from proxy_queue: dispatch_turn routes user_message
        # to proxy_queue per the routing table.
        msg = await store.lease("proxy_queue")
        assert msg is not None
        assert msg.message_kind == "user_message"
        assert msg.sender == "user"
        assert msg.trace_id == trace_id
        assert msg.payload["text"] == "hello"

    async def test_includes_metadata(self, store: DurableQueueStore, router: QueueRouter) -> None:
        orchestrator = QueueOrchestrator(store=store, router=router, consumers=[])
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)

        await bridge.dispatch_turn("test", trace_id="t1", metadata={"key": "value"})

        msg = await store.lease("proxy_queue")
        assert msg is not None
        assert msg.payload["metadata"] == {"key": "value"}


class TestQueueBridgeDispatchGoal:
    """Verify dispatch_goal enqueues plan_request to planner_queue."""

    async def test_enqueues_plan_request(self, store: DurableQueueStore, router: QueueRouter) -> None:
        orchestrator = QueueOrchestrator(store=store, router=router, consumers=[])
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)
        trace_id = str(uuid.uuid4())

        await bridge.dispatch_goal(
            goal_id="goal-1",
            goal_description="Refactor auth",
            trace_id=trace_id,
        )

        msg = await store.lease("planner_queue")
        assert msg is not None
        assert msg.message_kind == "plan_request"
        assert msg.sender == "runtime"
        assert msg.trace_id == trace_id
        assert msg.payload["goal_id"] == "goal-1"
        assert msg.payload["autonomous"] is True


class TestQueueBridgeCollectResponse:
    """Verify collect_response polls for matching agent_response."""

    async def test_returns_matching_response(self, store: DurableQueueStore, router: QueueRouter) -> None:
        orchestrator = QueueOrchestrator(store=store, router=router, consumers=[])
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)
        trace_id = "trace-123"

        # Simulate an agent_response arriving in proxy_queue.
        response_msg = QueueMessage(
            message_kind="agent_response",
            sender="proxy",
            trace_id=trace_id,
            payload={"text": "Here is your answer"},
        )
        await router.route(response_msg)

        result = await bridge.collect_response(trace_id=trace_id, timeout_s=2.0)
        assert result is not None
        assert result.payload["text"] == "Here is your answer"
        assert result.trace_id == trace_id

    async def test_returns_none_on_timeout(self, store: DurableQueueStore, router: QueueRouter) -> None:
        orchestrator = QueueOrchestrator(store=store, router=router, consumers=[])
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)

        result = await bridge.collect_response(trace_id="nonexistent", timeout_s=0.3)
        assert result is None


# ── Factory Tests ──────────────────────────────────────────────────────


class TestCreateQueueSystem:
    """Verify factory wires a working queue system."""

    async def test_creates_orchestrator_and_bridge(self, tmp_db: str) -> None:
        proxy = MockProxyAgent()
        planner = MockPlannerAgent()
        executor = MockExecutorAgent()

        orchestrator, bridge = await create_queue_system(
            db_path=tmp_db,
            proxy_agent=proxy,
            planner_agent=planner,
            executor_agent=executor,
        )

        assert isinstance(orchestrator, QueueOrchestrator)
        assert isinstance(bridge, QueueBridge)

    async def test_store_is_initialized(self, tmp_db: str) -> None:
        """Factory should initialize the store (tables exist)."""
        proxy = MockProxyAgent()
        planner = MockPlannerAgent()
        executor = MockExecutorAgent()

        _orchestrator, bridge = await create_queue_system(
            db_path=tmp_db,
            proxy_agent=proxy,
            planner_agent=planner,
            executor_agent=executor,
        )

        # Enqueuing should work without errors if tables exist.
        await bridge.dispatch_turn("test", trace_id="t1")

    async def test_orchestrator_can_start_and_stop(self, tmp_db: str) -> None:
        proxy = MockProxyAgent()
        planner = MockPlannerAgent()
        executor = MockExecutorAgent()

        orchestrator, _bridge = await create_queue_system(
            db_path=tmp_db,
            proxy_agent=proxy,
            planner_agent=planner,
            executor_agent=executor,
        )

        await orchestrator.start()
        assert orchestrator.running is True
        await orchestrator.stop()
        assert orchestrator.running is False


# ── Full Flow Tests ────────────────────────────────────────────────────


class TestFullQueueFlow:
    """End-to-end: user_message → proxy consumer → planner → executor → status."""

    async def test_user_message_processed_by_proxy(self, tmp_db: str) -> None:
        """Proxy consumer picks up user_message and processes it."""
        proxy = MockProxyAgent(route="direct")
        planner = MockPlannerAgent()
        executor = MockExecutorAgent()

        orchestrator, bridge = await create_queue_system(
            db_path=tmp_db,
            proxy_agent=proxy,
            planner_agent=planner,
            executor_agent=executor,
        )

        trace_id = str(uuid.uuid4())
        await bridge.dispatch_turn("What is 2+2?", trace_id=trace_id)

        # Start orchestrator briefly to let proxy consumer process.
        await orchestrator.start()
        # Why short sleep: give the consumer poll loop time to lease and
        # process the message. 0.5s is plenty for a mock agent.
        await asyncio.sleep(0.5)
        await orchestrator.stop()

        assert proxy.call_count == 1

    async def test_planner_route_triggers_plan_request(self, tmp_db: str) -> None:
        """When proxy routes to planner, a plan_request is enqueued."""
        proxy = MockProxyAgent(route="planner")
        planner = MockPlannerAgent()
        executor = MockExecutorAgent()

        orchestrator, bridge = await create_queue_system(
            db_path=tmp_db,
            proxy_agent=proxy,
            planner_agent=planner,
            executor_agent=executor,
        )

        trace_id = str(uuid.uuid4())
        await bridge.dispatch_turn("Refactor the auth module", trace_id=trace_id)

        await orchestrator.start()
        # Why 1s: proxy needs to process user_message, produce plan_request,
        # then planner needs to consume it. Two consumer cycles.
        await asyncio.sleep(1.0)
        await orchestrator.stop()

        assert proxy.call_count == 1
        assert planner.call_count >= 1


# ── Trace ID Propagation ──────────────────────────────────────────────


class TestTraceIdPropagation:
    """Verify trace_id flows across queue hops."""

    async def test_trace_id_preserved_on_dispatch_turn(
        self, store: DurableQueueStore, router: QueueRouter,
    ) -> None:
        orchestrator = QueueOrchestrator(store=store, router=router, consumers=[])
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)

        trace_id = "trace-propagation-test"
        await bridge.dispatch_turn("hello", trace_id=trace_id)

        msg = await store.lease("proxy_queue")
        assert msg is not None
        assert msg.trace_id == trace_id

    async def test_trace_id_preserved_on_dispatch_goal(
        self, store: DurableQueueStore, router: QueueRouter,
    ) -> None:
        orchestrator = QueueOrchestrator(store=store, router=router, consumers=[])
        bridge = QueueBridge(orchestrator=orchestrator, router=router, store=store)

        trace_id = "goal-trace-test"
        await bridge.dispatch_goal("g1", "Do something", trace_id=trace_id)

        msg = await store.lease("planner_queue")
        assert msg is not None
        assert msg.trace_id == trace_id

    async def test_proxy_to_planner_preserves_trace_id(self, tmp_db: str) -> None:
        """When proxy routes to planner, the plan_request keeps the same trace_id."""
        store = DurableQueueStore(tmp_db)
        await store.initialize()
        router = QueueRouter(store)

        proxy = MockProxyAgent(route="planner")
        proxy_consumer = ProxyConsumer(store, router, proxy)

        trace_id = "cross-hop-trace"
        user_msg = QueueMessage(
            message_kind="user_message",
            sender="user",
            trace_id=trace_id,
            payload={"text": "complex task"},
        )
        await router.route(user_msg)

        # Process the user_message through proxy consumer.
        await proxy_consumer.poll_once()

        # The proxy should have routed a plan_request to planner_queue.
        plan_msg = await store.lease("planner_queue")
        assert plan_msg is not None
        assert plan_msg.trace_id == trace_id
        assert plan_msg.message_kind == "plan_request"


# ── Backward Compatibility ─────────────────────────────────────────────


class TestBackwardCompatibility:
    """Stream without queue_bridge uses the legacy direct-call path."""

    def test_stream_defaults_to_no_bridge(self) -> None:
        """Stream.queue_bridge defaults to None (legacy mode)."""
        import dataclasses

        from silas.core.stream import Stream

        fields = {f.name: f for f in dataclasses.fields(Stream)}
        assert "queue_bridge" in fields
        assert fields["queue_bridge"].default is None
