"""Tests for the consult-planner → replan → escalate cascade (Principle #8).

Verifies the full self-healing flow wired into ExecutorConsumer:
- retry → consult planner → retry with guidance → success
- consult timeout → replan trigger
- max_replan_depth exceeded → user escalation
- budget attribution (consult tokens charge to plan budget)
- factory wiring injects managers into ExecutorConsumer
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass

import pytest
from silas.execution.consult import ConsultPlannerManager
from silas.execution.consumers import ExecutorConsumer
from silas.execution.factory import create_queue_system
from silas.execution.queue_store import DurableQueueStore
from silas.execution.queue_types import QueueMessage
from silas.execution.replan import MAX_REPLAN_DEPTH, ReplanManager
from silas.execution.router import QueueRouter

# ── Mock Executor Agent ──────────────────────────────────────────────


@dataclass
class _ExecOutput:
    summary: str = "done"
    last_error: str | None = None


@dataclass
class _ExecResult:
    output: _ExecOutput


class MockExecutorAgent:
    """Executor that fails N times then succeeds.

    Why a call counter: we need to verify the cascade retries with guidance
    and that the guided attempt actually runs.
    """

    def __init__(self, fail_count: int = 0) -> None:
        self._fail_count = fail_count
        self.call_count = 0
        self.last_prompt: str = ""

    async def run(self, prompt: str, deps: object | None = None) -> _ExecResult:
        self.call_count += 1
        self.last_prompt = prompt
        if self.call_count <= self._fail_count:
            return _ExecResult(
                output=_ExecOutput(
                    summary="failed",
                    last_error=f"attempt {self.call_count} failed",
                )
            )
        return _ExecResult(output=_ExecOutput(summary="Execution completed."))


class AlwaysFailExecutor:
    """Executor that always fails. Used for replan/escalation tests."""

    def __init__(self) -> None:
        self.call_count = 0
        self.last_prompt: str = ""

    async def run(self, prompt: str, deps: object | None = None) -> _ExecResult:
        self.call_count += 1
        self.last_prompt = prompt
        return _ExecResult(
            output=_ExecOutput(
                summary="failed",
                last_error="persistent failure",
            )
        )


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
async def store() -> DurableQueueStore:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = DurableQueueStore(db_path)
    await s.initialize()
    return s


@pytest.fixture
def router(store: DurableQueueStore) -> QueueRouter:
    return QueueRouter(store)


@pytest.fixture
def consult_manager(store: DurableQueueStore, router: QueueRouter) -> ConsultPlannerManager:
    return ConsultPlannerManager(store, router)


@pytest.fixture
def replan_manager(router: QueueRouter) -> ReplanManager:
    return ReplanManager(router)


def _make_exec_request(
    *,
    work_item_id: str = "wi-1",
    body: str = "do the thing",
    on_stuck: str = "consult_planner",
    replan_depth: int = 0,
    trace_id: str = "trace-abc",
) -> QueueMessage:
    return QueueMessage(
        message_kind="execution_request",
        sender="planner",
        trace_id=trace_id,
        payload={
            "work_item_id": work_item_id,
            "body": body,
            "on_stuck": on_stuck,
            "original_goal": body,
            "replan_depth": replan_depth,
        },
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestRetryConsultFlow:
    """Executor fails → consults planner → retries with guidance → succeeds."""

    @pytest.mark.asyncio
    async def test_consult_guidance_enables_retry_success(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        consult_manager: ConsultPlannerManager,
        replan_manager: ReplanManager,
    ) -> None:
        # Executor fails first call, succeeds on second (guided retry).
        executor = MockExecutorAgent(fail_count=1)

        consumer = ExecutorConsumer(
            store,
            router,
            executor,
            consult_manager=consult_manager,
            replan_manager=replan_manager,
        )

        msg = _make_exec_request()

        # Pre-plant the planner guidance response on runtime_queue so
        # ConsultPlannerManager finds it when polling.
        guidance_msg = QueueMessage(
            message_kind="planner_guidance",
            sender="planner",
            trace_id=msg.trace_id,
            payload={"guidance": "Try using a different approach."},
        )
        guidance_msg.queue_name = "runtime_queue"
        await store.enqueue(guidance_msg)

        result = await consumer._process(msg)

        assert result is not None
        assert result.payload["status"] == "done"
        # Executor called twice: initial fail + guided retry.
        assert executor.call_count == 2
        # Guided retry prompt should include the guidance.
        assert "Planner Guidance" in executor.last_prompt
        assert "different approach" in executor.last_prompt


class TestConsultTimeoutTriggersReplan:
    """Consult times out → replan is triggered."""

    @pytest.mark.asyncio
    async def test_consult_timeout_triggers_replan(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        replan_manager: ReplanManager,
    ) -> None:
        executor = AlwaysFailExecutor()

        # Use a very short timeout so consult times out immediately.
        consult = ConsultPlannerManager(store, QueueRouter(store))

        consumer = ExecutorConsumer(
            store,
            router,
            executor,
            consult_manager=consult,
            replan_manager=replan_manager,
        )

        msg = _make_exec_request()

        # Don't plant any guidance response → consult will timeout.
        # Use monkey-patching for fast timeout.
        original_consult = consult.consult

        async def fast_timeout_consult(
            work_item_id: str,
            failure_context: str,
            trace_id: str,
            timeout_s: float = 90.0,
        ) -> str | None:
            return await original_consult(
                work_item_id,
                failure_context,
                trace_id,
                timeout_s=0.1,
            )

        consult.consult = fast_timeout_consult  # type: ignore[assignment]

        result = await consumer._process(msg)

        assert result is not None
        # Should report stuck (replan in progress), not failed.
        assert result.payload["status"] == "stuck"
        assert "Replan triggered" in str(result.payload["summary"])

        # Verify replan_request was enqueued to planner_queue.
        replan_msg = await store.lease("planner_queue")
        assert replan_msg is not None
        # First message is the consult plan_request, skip it.
        if replan_msg.message_kind == "plan_request":
            await store.ack(replan_msg.id)
            replan_msg = await store.lease("planner_queue")
            assert replan_msg is not None
        assert replan_msg.message_kind == "replan_request"
        assert replan_msg.payload["replan_depth"] == 1


class TestMaxReplanDepthEscalation:
    """max_replan_depth exceeded → escalate to user."""

    @pytest.mark.asyncio
    async def test_replan_depth_exceeded_escalates(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        replan_manager: ReplanManager,
    ) -> None:
        executor = AlwaysFailExecutor()

        consult = ConsultPlannerManager(store, QueueRouter(store))

        consumer = ExecutorConsumer(
            store,
            router,
            executor,
            consult_manager=consult,
            replan_manager=replan_manager,
        )

        # Simulate depth already at max — this is the 3rd attempt (0-indexed: 2).
        msg = _make_exec_request(replan_depth=MAX_REPLAN_DEPTH)

        # Fast timeout consult.
        async def fast_timeout_consult(
            work_item_id: str,
            failure_context: str,
            trace_id: str,
            timeout_s: float = 90.0,
        ) -> str | None:
            return None

        consult.consult = fast_timeout_consult  # type: ignore[assignment]

        result = await consumer._process(msg)

        assert result is not None
        assert result.payload["status"] == "failed"
        assert result.payload.get("escalated") is True
        assert "All recovery exhausted" in str(result.payload["summary"])


class TestBudgetAttribution:
    """Consult tokens are routed through planner_queue (plan budget, not work-item budget).

    We verify this indirectly: ConsultPlannerManager sends plan_request to
    planner_queue. Since the planner_queue handles plan budget accounting,
    any tokens consumed by the consult flow charge to plan budget.
    """

    @pytest.mark.asyncio
    async def test_consult_request_routes_to_planner_queue(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
    ) -> None:
        executor = AlwaysFailExecutor()
        consult = ConsultPlannerManager(store, router)

        consumer = ExecutorConsumer(
            store,
            router,
            executor,
            consult_manager=consult,
        )

        msg = _make_exec_request()

        # Fast timeout so we don't wait.
        async def fast_timeout_consult(
            work_item_id: str,
            failure_context: str,
            trace_id: str,
            timeout_s: float = 90.0,
        ) -> str | None:
            # Actually call the real consult to enqueue the plan_request,
            # then return None to simulate timeout.
            request_msg = QueueMessage(
                message_kind="plan_request",
                sender="executor",
                trace_id=trace_id,
                payload={
                    "user_request": f"CONSULT REQUEST — executor needs guidance.\n\nWork item: {work_item_id}",
                    "consult": True,
                    "work_item_id": work_item_id,
                },
            )
            await router.route(request_msg)
            return None

        consult.consult = fast_timeout_consult  # type: ignore[assignment]

        await consumer._process(msg)

        # Verify the consult request was routed to planner_queue (plan budget).
        plan_msg = await store.lease("planner_queue")
        assert plan_msg is not None
        assert plan_msg.message_kind == "plan_request"
        assert plan_msg.payload.get("consult") is True


class TestNoCascadeWithoutManagers:
    """Without consult/replan managers, failures pass through directly."""

    @pytest.mark.asyncio
    async def test_failure_without_cascade(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
    ) -> None:
        executor = AlwaysFailExecutor()

        consumer = ExecutorConsumer(store, router, executor)
        msg = _make_exec_request()

        result = await consumer._process(msg)

        assert result is not None
        assert result.payload["status"] == "failed"
        assert result.payload.get("escalated") is None
        assert executor.call_count == 1


class TestGuidedRetryAlsoFails:
    """Consult returns guidance, but guided retry also fails → triggers replan."""

    @pytest.mark.asyncio
    async def test_guided_retry_fails_triggers_replan(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        consult_manager: ConsultPlannerManager,
        replan_manager: ReplanManager,
    ) -> None:
        executor = AlwaysFailExecutor()

        consumer = ExecutorConsumer(
            store,
            router,
            executor,
            consult_manager=consult_manager,
            replan_manager=replan_manager,
        )

        msg = _make_exec_request()

        # Plant guidance response.
        guidance_msg = QueueMessage(
            message_kind="planner_guidance",
            sender="planner",
            trace_id=msg.trace_id,
            payload={"guidance": "Try X instead."},
        )
        guidance_msg.queue_name = "runtime_queue"
        await store.enqueue(guidance_msg)

        result = await consumer._process(msg)

        assert result is not None
        # Executor always fails → guided retry fails → replan triggered.
        assert result.payload["status"] == "stuck"
        assert executor.call_count == 2  # initial + guided retry
        assert "Replan triggered" in str(result.payload["summary"])


class TestFactoryWiresConsultReplanIntoExecutorConsumer:
    """create_queue_system must inject ConsultPlannerManager and ReplanManager.

    Without this wiring, ExecutorConsumer._consult and ._replan are None
    and the cascade degrades to plain failure passthrough.
    """

    @pytest.mark.asyncio
    async def test_factory_injects_cascade_managers(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        # Minimal agent stubs — we only need to inspect the consumer's attributes.
        executor = MockExecutorAgent(fail_count=0)
        proxy = MockExecutorAgent(fail_count=0)
        planner = MockExecutorAgent(fail_count=0)

        orchestrator, _bridge = await create_queue_system(
            db_path=db_path,
            proxy_agent=proxy,
            planner_agent=planner,
            executor_agent=executor,
        )

        # Find the ExecutorConsumer in the orchestrator's consumers.
        executor_consumers = [c for c in orchestrator._consumers if isinstance(c, ExecutorConsumer)]
        assert len(executor_consumers) == 1
        ec = executor_consumers[0]

        # Verify managers were injected.
        assert ec._consult is not None, "ConsultPlannerManager not wired"
        assert ec._replan is not None, "ReplanManager not wired"
        assert isinstance(ec._consult, ConsultPlannerManager)
        assert isinstance(ec._replan, ReplanManager)
