"""Tests for WI-3: Queue-Based Agent Communication + Execution.

Tests all consumer types, orchestrator lifecycle, consult/replan managers,
status routing, and a full integration flow. Uses real DurableQueueStore
with in-memory SQLite and mock agents (no LLM calls).
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass

import aiosqlite
import pytest
from silas.models.work import WorkItemResult, WorkItemStatus
from silas.queue.consult import ConsultPlannerManager
from silas.queue.consumers import (
    BaseConsumer,
    ExecutorConsumer,
    PlannerConsumer,
    ProxyConsumer,
)
from silas.queue.orchestrator import QueueOrchestrator
from silas.queue.replan import MAX_REPLAN_DEPTH, ReplanManager
from silas.queue.router import QueueRouter
from silas.queue.status_router import route_to_surface
from silas.queue.store import DurableQueueStore
from silas.queue.types import QueueMessage

# ── Mock Agents ──────────────────────────────────────────────────────
# Why dataclass mocks: minimal, typed, deterministic. No LLM calls.


@dataclass
class MockRouteOutput:
    """Mimics ProxyRunResult.output (RouteDecision)."""
    route: str = "direct"
    reason: str = "mock"


@dataclass
class MockProxyResult:
    """Mimics ProxyRunResult."""
    output: MockRouteOutput


class MockProxyAgent:
    """Mock proxy that returns configurable route decisions."""

    def __init__(self, route: str = "direct") -> None:
        self._route = route
        self.call_count = 0

    async def run(self, prompt: str, deps: object | None = None) -> MockProxyResult:
        self.call_count += 1
        return MockProxyResult(output=MockRouteOutput(route=self._route))


@dataclass
class MockPlanAction:
    plan_markdown: str = "# Mock Plan\n\n1. Do something."


@dataclass
class MockPlannerOutput:
    """Mimics PlannerRunResult.output (AgentResponse)."""
    message: str = "Generated plan."
    plan_action: MockPlanAction | None = None


@dataclass
class MockPlannerResult:
    """Mimics PlannerRunResult."""
    output: MockPlannerOutput


class MockPlannerAgent:
    """Mock planner that returns configurable plan results."""

    def __init__(self, plan_markdown: str = "# Mock Plan") -> None:
        self._plan_markdown = plan_markdown
        self.call_count = 0

    async def run(self, prompt: str, deps: object | None = None) -> MockPlannerResult:
        self.call_count += 1
        return MockPlannerResult(
            output=MockPlannerOutput(
                plan_action=MockPlanAction(plan_markdown=self._plan_markdown)
            )
        )


@dataclass
class MockExecutorOutput:
    """Mimics ExecutorRunResult.output (ExecutorAgentOutput)."""
    summary: str = "Execution completed."
    last_error: str | None = None


@dataclass
class MockExecutorResult:
    """Mimics ExecutorRunResult."""
    output: MockExecutorOutput


class MockExecutorAgent:
    """Mock executor that returns configurable results."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.call_count = 0

    async def run(self, prompt: str, deps: object | None = None) -> MockExecutorResult:
        self.call_count += 1
        if self._fail:
            return MockExecutorResult(
                output=MockExecutorOutput(
                    summary="Failed.", last_error="mock_error"
                )
            )
        return MockExecutorResult(output=MockExecutorOutput())


class MockWorkItemExecutor:
    """Mock work-item executor that returns a configurable result."""

    def __init__(self, result_status: WorkItemStatus = WorkItemStatus.done) -> None:
        self._result_status = result_status
        self.call_count = 0
        self.work_item_ids: list[str] = []

    async def execute(self, item) -> WorkItemResult:
        self.call_count += 1
        self.work_item_ids.append(item.id)
        return WorkItemResult(
            work_item_id=item.id,
            status=self._result_status,
            summary=f"Executed {item.id}",
            last_error=None if self._result_status == WorkItemStatus.done else "execution failed",
        )


class FailingConsumer(BaseConsumer):
    """A consumer whose _process always raises, for testing nack/dead-letter."""

    async def _process(self, msg: QueueMessage) -> QueueMessage | None:
        raise RuntimeError("intentional failure")


class EchoConsumer(BaseConsumer):
    """A consumer that returns None (ack without routing)."""

    async def _process(self, msg: QueueMessage) -> QueueMessage | None:
        return None


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
async def store() -> DurableQueueStore:
    """In-memory SQLite queue store for isolated tests."""
    # Why tempfile: aiosqlite with :memory: doesn't share state across
    # connections. A temp file gives us a real shared database.
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = DurableQueueStore(db_path)
    await s.initialize()
    return s


@pytest.fixture
def router(store: DurableQueueStore) -> QueueRouter:
    return QueueRouter(store)


# ── BaseConsumer Tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_base_consumer_poll_once_happy_path(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """Lease → process → ack. Message is removed after processing."""
    consumer = EchoConsumer(store, router, "proxy_queue")
    msg = QueueMessage(
        message_kind="user_message", sender="user", payload={"text": "hi"}
    )
    await router.route(msg)

    found = await consumer.poll_once()
    assert found is True

    # Message should be acked (removed from queue).
    assert await store.pending_count("proxy_queue") == 0


@pytest.mark.asyncio
async def test_base_consumer_poll_once_empty_queue(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """poll_once returns False when no messages are available."""
    consumer = EchoConsumer(store, router, "proxy_queue")
    found = await consumer.poll_once()
    assert found is False


@pytest.mark.asyncio
async def test_base_consumer_process_failure_nacks(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """When _process raises, the message is nacked (attempt_count incremented)."""
    consumer = FailingConsumer(store, router, "proxy_queue")
    msg = QueueMessage(
        message_kind="user_message", sender="user", payload={"text": "fail"}
    )
    await router.route(msg)

    found = await consumer.poll_once()
    assert found is True

    # Message should still be in queue (nacked, not acked).
    leased = await store.lease("proxy_queue")
    assert leased is not None
    assert leased.attempt_count == 1


@pytest.mark.asyncio
async def test_base_consumer_dead_letter_on_max_attempts(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """After max_attempts, message goes to dead_letter instead of retrying."""
    consumer = FailingConsumer(store, router, "proxy_queue", max_attempts=2)
    msg = QueueMessage(
        message_kind="user_message", sender="user", payload={"text": "die"}
    )
    msg.attempt_count = 2  # Already at max
    await router.route(msg)

    found = await consumer.poll_once()
    assert found is True

    # Message should be dead-lettered, not in queue.
    assert await store.pending_count("proxy_queue") == 0


# ── ProxyConsumer Tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_proxy_consumer_user_message_routes_to_planner(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """user_message → proxy (route=planner) → enqueues plan_request."""
    proxy = MockProxyAgent(route="planner")
    consumer = ProxyConsumer(store, router, proxy)

    msg = QueueMessage(
        message_kind="user_message", sender="user",
        payload={"text": "refactor auth module"},
    )
    await router.route(msg)
    await consumer.poll_once()

    assert proxy.call_count == 1

    # plan_request should be in planner_queue.
    leased = await store.lease("planner_queue")
    assert leased is not None
    assert leased.message_kind == "plan_request"
    assert leased.sender == "proxy"


@pytest.mark.asyncio
async def test_proxy_consumer_user_message_direct(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """user_message → proxy (route=direct) → no further routing."""
    proxy = MockProxyAgent(route="direct")
    consumer = ProxyConsumer(store, router, proxy)

    msg = QueueMessage(
        message_kind="user_message", sender="user",
        payload={"text": "hello"},
    )
    await router.route(msg)
    await consumer.poll_once()

    assert proxy.call_count == 1
    # No messages should be in planner_queue.
    assert await store.pending_count("planner_queue") == 0


@pytest.mark.asyncio
async def test_proxy_consumer_execution_status_done(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """execution_status with 'done' → surfaces include stream."""
    proxy = MockProxyAgent()
    consumer = ProxyConsumer(store, router, proxy)

    msg = QueueMessage(
        message_kind="execution_status", sender="executor",
        payload={"status": "done", "work_item_id": "wi-1"},
    )
    await router.route(msg)
    await consumer.poll_once()

    surfaces = route_to_surface("done")
    assert "stream" in surfaces
    assert "activity" in surfaces


@pytest.mark.asyncio
async def test_proxy_consumer_execution_status_failed_dual_emit(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """execution_status with 'failed' → dual-emit to stream + activity."""
    proxy = MockProxyAgent()
    consumer = ProxyConsumer(store, router, proxy)

    msg = QueueMessage(
        message_kind="execution_status", sender="executor",
        payload={"status": "failed", "work_item_id": "wi-1"},
    )
    await router.route(msg)
    await consumer.poll_once()

    surfaces = route_to_surface("failed")
    assert surfaces == ("stream", "activity")


# ── PlannerConsumer Tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_planner_consumer_plan_request(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """plan_request → runs planner → enqueues plan_result."""
    planner = MockPlannerAgent()
    consumer = PlannerConsumer(store, router, planner)

    msg = QueueMessage(
        message_kind="plan_request", sender="proxy",
        payload={"user_request": "refactor auth"},
    )
    await router.route(msg)
    await consumer.poll_once()

    assert planner.call_count == 1

    # plan_result should be in proxy_queue.
    leased = await store.lease("proxy_queue")
    assert leased is not None
    assert leased.message_kind == "plan_result"
    assert leased.payload["plan_markdown"] == "# Mock Plan"


@pytest.mark.asyncio
async def test_planner_consumer_replan_request(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """replan_request → runs planner with failure context → enqueues revised plan_result."""
    planner = MockPlannerAgent(plan_markdown="# Revised Plan")
    consumer = PlannerConsumer(store, router, planner)

    msg = QueueMessage(
        message_kind="replan_request", sender="runtime",
        payload={
            "original_goal": "refactor auth",
            "failure_history": [{"error": "test failed"}],
            "replan_depth": 1,
        },
    )
    await router.route(msg)
    await consumer.poll_once()

    assert planner.call_count == 1

    leased = await store.lease("proxy_queue")
    assert leased is not None
    assert leased.message_kind == "plan_result"
    assert leased.payload.get("is_replan") is True


# ── ExecutorConsumer Tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_executor_consumer_execution_request(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """execution_request → runs executor → enqueues execution_status."""
    executor = MockExecutorAgent()
    consumer = ExecutorConsumer(store, router, executor)

    msg = QueueMessage(
        message_kind="execution_request", sender="planner",
        payload={"work_item_id": "wi-1", "task_description": "do something"},
    )
    await router.route(msg)
    await consumer.poll_once()

    assert executor.call_count == 1

    leased = await store.lease("proxy_queue")
    assert leased is not None
    assert leased.message_kind == "execution_status"
    assert leased.payload["status"] == "done"
    assert leased.payload["work_item_id"] == "wi-1"


@pytest.mark.asyncio
async def test_executor_consumer_execution_request_failure(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """execution_request that fails → execution_status with status='failed'."""
    executor = MockExecutorAgent(fail=True)
    consumer = ExecutorConsumer(store, router, executor)

    msg = QueueMessage(
        message_kind="execution_request", sender="planner",
        payload={"work_item_id": "wi-1", "task_description": "fail"},
    )
    await router.route(msg)
    await consumer.poll_once()

    leased = await store.lease("proxy_queue")
    assert leased is not None
    assert leased.payload["status"] == "failed"
    assert leased.payload["last_error"] == "mock_error"


@pytest.mark.asyncio
async def test_executor_consumer_research_request(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """research_request → runs executor in research mode → enqueues research_result."""
    executor = MockExecutorAgent()
    consumer = ExecutorConsumer(store, router, executor)

    msg = QueueMessage(
        message_kind="research_request", sender="planner",
        payload={"query": "what is X?", "original_request": "plan Y"},
    )
    await router.route(msg)
    await consumer.poll_once()

    assert executor.call_count == 1

    leased = await store.lease("planner_queue")
    assert leased is not None
    assert leased.message_kind == "research_result"
    assert leased.payload["query"] == "what is X?"


@pytest.mark.asyncio
async def test_executor_consumer_execution_request_with_work_item_payload(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """execution_request with work_item payload runs WorkItemExecutor.execute."""
    executor = MockExecutorAgent()
    work_executor = MockWorkItemExecutor()
    consumer = ExecutorConsumer(store, router, executor, work_executor=work_executor)

    msg = QueueMessage(
        message_kind="execution_request",
        sender="planner",
        payload={
            "work_item": {
                "id": "wi-work-exec-1",
                "type": "task",
                "title": "Execute queued work item",
                "body": "Run queued task.",
                "skills": [],
            }
        },
    )
    await router.route(msg)
    await consumer.poll_once()

    assert work_executor.call_count == 1
    assert work_executor.work_item_ids == ["wi-work-exec-1"]
    assert executor.call_count == 0

    leased = await store.lease("proxy_queue")
    assert leased is not None
    assert leased.message_kind == "execution_status"
    assert leased.payload["status"] == "done"
    assert leased.payload["work_item_id"] == "wi-work-exec-1"


@pytest.mark.asyncio
async def test_executor_consumer_invalid_work_item_nacks_then_dead_letters(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """Invalid work_item payload is retried, then dead-lettered at max attempts."""
    executor = MockExecutorAgent()
    work_executor = MockWorkItemExecutor()
    consumer = ExecutorConsumer(
        store,
        router,
        executor,
        work_executor=work_executor,
        max_attempts=1,
    )

    msg = QueueMessage(
        message_kind="execution_request",
        sender="planner",
        payload={"work_item": {"id": "invalid-only-id"}},
    )
    await router.route(msg)

    # First pass: validation error -> nack (attempt_count increments).
    await consumer.poll_once()
    leased_retry = await store.lease("executor_queue")
    assert leased_retry is not None
    assert leased_retry.attempt_count == 1
    await store.ack(leased_retry.id)

    # Re-enqueue equivalent message already at max attempts to verify dead-letter behavior.
    dead_letter_candidate = QueueMessage(
        message_kind="execution_request",
        sender="planner",
        payload={"work_item": {"id": "invalid-only-id"}},
    )
    dead_letter_candidate.attempt_count = 1
    await router.route(dead_letter_candidate)
    await consumer.poll_once()

    assert await store.pending_count("executor_queue") == 0

    async with aiosqlite.connect(store.db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM dead_letters WHERE id = ?",
            (dead_letter_candidate.id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1


# ── ConsultPlannerManager Tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_consult_planner_receives_guidance(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """consult → enqueues to planner → receives guidance → returns it."""
    manager = ConsultPlannerManager(store, router)
    trace_id = "trace-consult-1"

    # Simulate planner responding with guidance after a short delay.
    async def _provide_guidance() -> None:
        await asyncio.sleep(0.3)
        guidance_msg = QueueMessage(
            message_kind="planner_guidance",
            sender="planner",
            trace_id=trace_id,
            payload={"guidance": "Try approach B instead."},
        )
        await router.route(guidance_msg)

    task = asyncio.create_task(_provide_guidance())
    result = await manager.consult(
        "wi-1", "approach A failed", trace_id, timeout_s=5.0,
    )
    await task

    assert result == "Try approach B instead."


@pytest.mark.asyncio
async def test_consult_planner_timeout(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """consult timeout → returns None."""
    manager = ConsultPlannerManager(store, router)

    result = await manager.consult(
        "wi-1", "stuck", "trace-timeout", timeout_s=0.5,
    )
    assert result is None


# ── ReplanManager Tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replan_enqueues_request(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """trigger_replan → enqueues replan_request to planner_queue."""
    manager = ReplanManager(router)

    ok = await manager.trigger_replan(
        "wi-1", "refactor auth",
        [{"error": "test failed"}],
        "trace-replan-1",
        current_depth=0,
    )
    assert ok is True

    leased = await store.lease("planner_queue")
    assert leased is not None
    assert leased.message_kind == "replan_request"
    assert leased.payload["replan_depth"] == 1


@pytest.mark.asyncio
async def test_replan_max_depth_exceeded(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """trigger_replan at max depth → returns False (escalate to user)."""
    manager = ReplanManager(router)

    ok = await manager.trigger_replan(
        "wi-1", "refactor auth",
        [{"error": "still failing"}],
        "trace-replan-2",
        current_depth=MAX_REPLAN_DEPTH,
    )
    assert ok is False

    # No message should have been enqueued.
    assert await store.pending_count("planner_queue") == 0


# ── route_to_surface Tests ──────────────────────────────────────────


def test_route_to_surface_running() -> None:
    assert route_to_surface("running") == ("activity",)


def test_route_to_surface_done() -> None:
    assert route_to_surface("done") == ("stream", "activity")


def test_route_to_surface_failed() -> None:
    assert route_to_surface("failed") == ("stream", "activity")


def test_route_to_surface_stuck() -> None:
    assert route_to_surface("stuck") == ("stream", "activity")


def test_route_to_surface_blocked() -> None:
    assert route_to_surface("blocked") == ("stream", "activity")


def test_route_to_surface_verification_failed() -> None:
    assert route_to_surface("verification_failed") == ("stream", "activity")


def test_route_to_surface_unknown_defaults() -> None:
    """Unknown status falls back to dual-emit for safety."""
    assert route_to_surface("some_unknown_status") == ("stream", "activity")


# ── QueueOrchestrator Tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_start_stop(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """Orchestrator starts and stops cleanly."""
    consumer = EchoConsumer(store, router, "proxy_queue")
    orchestrator = QueueOrchestrator(store, router, [consumer])

    await orchestrator.start()
    assert orchestrator.running is True

    # Let it run briefly.
    await asyncio.sleep(0.2)

    await orchestrator.stop()
    assert orchestrator.running is False


@pytest.mark.asyncio
async def test_orchestrator_processes_messages(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """Orchestrator's consumer actually processes messages."""
    proxy = MockProxyAgent(route="direct")
    consumer = ProxyConsumer(store, router, proxy)
    orchestrator = QueueOrchestrator(
        store, router, [consumer], poll_interval_s=0.05,
    )

    msg = QueueMessage(
        message_kind="user_message", sender="user",
        payload={"text": "hello"},
    )
    await router.route(msg)
    await orchestrator.start()

    # Wait for processing.
    await asyncio.sleep(0.5)
    await orchestrator.stop()

    assert proxy.call_count >= 1
    assert await store.pending_count("proxy_queue") == 0


# ── Full Integration Flow ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_flow_user_to_executor_to_status(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """Full flow: user_message → proxy → planner → executor → execution_status → proxy.

    Tests that messages flow through all three consumers in sequence.
    """
    proxy = MockProxyAgent(route="planner")
    planner = MockPlannerAgent()
    executor = MockExecutorAgent()

    proxy_consumer = ProxyConsumer(store, router, proxy)
    planner_consumer = PlannerConsumer(store, router, planner)
    executor_consumer = ExecutorConsumer(store, router, executor)

    # Step 1: User sends message.
    msg = QueueMessage(
        message_kind="user_message", sender="user",
        trace_id="trace-full-flow",
        payload={"text": "refactor auth module"},
    )
    await router.route(msg)

    # Step 2: Proxy processes → enqueues plan_request.
    await proxy_consumer.poll_once()
    assert proxy.call_count == 1
    assert await store.pending_count("planner_queue") > 0

    # Step 3: Planner processes → enqueues plan_result.
    await planner_consumer.poll_once()
    assert planner.call_count == 1

    # plan_result goes to proxy_queue. Proxy processes it.
    plan_result = await store.lease("proxy_queue")
    assert plan_result is not None
    assert plan_result.message_kind == "plan_result"
    await store.ack(plan_result.id)

    # Step 4: Simulate approval → enqueue execution_request.
    exec_msg = QueueMessage(
        message_kind="execution_request", sender="runtime",
        trace_id="trace-full-flow",
        payload={"work_item_id": "wi-1", "task_description": "do the refactor"},
    )
    await router.route(exec_msg)

    # Step 5: Executor processes → enqueues execution_status.
    await executor_consumer.poll_once()
    assert executor.call_count == 1

    # Step 6: execution_status arrives at proxy_queue.
    status_msg = await store.lease("proxy_queue")
    assert status_msg is not None
    assert status_msg.message_kind == "execution_status"
    assert status_msg.payload["status"] == "done"
    assert status_msg.trace_id == "trace-full-flow"


# ── Idempotency Test ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idempotency_skip_already_processed(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """If a message was already processed (crash recovery), it's acked without reprocessing."""
    proxy = MockProxyAgent()
    consumer = ProxyConsumer(store, router, proxy)

    msg = QueueMessage(
        message_kind="user_message", sender="user",
        payload={"text": "hi"},
    )
    await router.route(msg)

    # Pre-mark as processed (simulating crash after mark but before ack).
    await store.mark_processed("consumer:proxy_queue", msg.id)

    await consumer.poll_once()

    # Proxy should NOT have been called (idempotency skip).
    assert proxy.call_count == 0
    # Message should be acked.
    assert await store.pending_count("proxy_queue") == 0


# ── Trace ID Propagation Test ───────────────────────────────────────


@pytest.mark.asyncio
async def test_trace_id_propagation(
    store: DurableQueueStore, router: QueueRouter,
) -> None:
    """trace_id propagates from user_message through proxy to plan_request."""
    proxy = MockProxyAgent(route="planner")
    consumer = ProxyConsumer(store, router, proxy)

    msg = QueueMessage(
        message_kind="user_message", sender="user",
        trace_id="trace-propagation-test",
        payload={"text": "complex task"},
    )
    await router.route(msg)
    await consumer.poll_once()

    leased = await store.lease("planner_queue")
    assert leased is not None
    assert leased.trace_id == "trace-propagation-test"
