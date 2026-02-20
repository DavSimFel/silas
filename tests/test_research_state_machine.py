"""Tests for the planner research state machine (§4.8).

Covers state transitions, in-flight cap, timeout/expiry, deduplication,
replay handling, and full flow integration with PlannerConsumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from silas.execution.consumers import PlannerConsumer
from silas.execution.queue_store import DurableQueueStore
from silas.execution.queue_types import QueueMessage
from silas.execution.research import ResearchState, ResearchStateMachine
from silas.execution.router import QueueRouter

# ── State Machine Unit Tests ─────────────────────────────────────────


class TestResearchStateMachine:
    """Core state transition and constraint tests."""

    def test_initial_state_is_planning(self) -> None:
        sm = ResearchStateMachine()
        assert sm.state == ResearchState.planning

    def test_planning_to_awaiting_on_first_request(self) -> None:
        sm = ResearchStateMachine()
        ok = sm.request_research("r1", "what is X?", "short", 100, now=0.0)
        assert ok
        assert sm.state == ResearchState.awaiting_research
        assert sm.in_flight_count == 1

    def test_awaiting_to_finalize_on_all_results(self) -> None:
        sm = ResearchStateMachine()
        sm.request_research("r1", "q1", "fmt", 100, now=0.0)
        sm.request_research("r2", "q2", "fmt", 100, now=0.0)
        assert sm.state == ResearchState.awaiting_research

        sm.receive_result("r1", "answer1")
        # Still waiting for r2
        assert sm.state == ResearchState.awaiting_research

        sm.receive_result("r2", "answer2")
        assert sm.state == ResearchState.ready_to_finalize
        assert sm.results == {"r1": "answer1", "r2": "answer2"}

    def test_in_flight_cap_rejects_fourth_request(self) -> None:
        sm = ResearchStateMachine(max_in_flight=3)
        assert sm.request_research("r1", "q1", "f", 100, now=0.0)
        assert sm.request_research("r2", "q2", "f", 100, now=0.0)
        assert sm.request_research("r3", "q3", "f", 100, now=0.0)
        # 4th should be rejected
        assert not sm.request_research("r4", "q4", "f", 100, now=0.0)
        assert sm.in_flight_count == 3

    def test_round_cap_enforced(self) -> None:
        """After max_rounds total dispatches, no more requests accepted."""
        sm = ResearchStateMachine(max_rounds=2)
        assert sm.request_research("r1", "q1", "f", 100, now=0.0)
        sm.receive_result("r1", "a1")
        # Finalize to get back to planning
        sm.finalize()
        # r2 is the 2nd round
        assert sm.request_research("r2", "q2", "f", 100, now=0.0)
        sm.receive_result("r2", "a2")
        sm.finalize()
        # 3rd should be rejected by round cap
        assert not sm.request_research("r3", "q3", "f", 100, now=0.0)

    def test_deduplication_rejects_identical_request(self) -> None:
        sm = ResearchStateMachine()
        assert sm.request_research("r1", "same query", "same fmt", 100, now=0.0)
        # Exact same query/format/tokens → rejected
        assert not sm.request_research("r2", "same query", "same fmt", 100, now=0.0)
        assert sm.in_flight_count == 1

    def test_deduplication_allows_different_format(self) -> None:
        sm = ResearchStateMachine()
        assert sm.request_research("r1", "same query", "short", 100, now=0.0)
        # Different format → different dedupe key → allowed
        assert sm.request_research("r2", "same query", "detailed", 100, now=0.0)
        assert sm.in_flight_count == 2

    def test_timeout_expires_stale_requests(self) -> None:
        sm = ResearchStateMachine(timeout_s=10.0)
        sm.request_research("r1", "q1", "f", 100, now=0.0)
        sm.request_research("r2", "q2", "f", 100, now=0.0)

        # r1 times out, r2 doesn't
        expired = sm.check_timeouts(now=11.0)
        assert "r1" in expired
        assert "r2" in expired  # both dispatched at 0.0
        # No results at all → expired state
        assert sm.state == ResearchState.expired

    def test_timeout_with_partial_results_goes_to_finalize(self) -> None:
        sm = ResearchStateMachine(timeout_s=10.0)
        sm.request_research("r1", "q1", "f", 100, now=0.0)
        sm.request_research("r2", "q2", "f", 100, now=0.0)

        # r1 completes, r2 will timeout
        sm.receive_result("r1", "answer1")
        expired = sm.check_timeouts(now=11.0)
        assert "r2" in expired
        # Has partial results → ready_to_finalize, not expired
        assert sm.state == ResearchState.ready_to_finalize

    def test_force_expire(self) -> None:
        sm = ResearchStateMachine()
        sm.request_research("r1", "q1", "f", 100, now=0.0)
        sm.force_expire()
        assert sm.state == ResearchState.expired
        assert sm.in_flight_count == 0

    def test_finalize_returns_results_and_resets_to_planning(self) -> None:
        sm = ResearchStateMachine()
        sm.request_research("r1", "q1", "f", 100, now=0.0)
        sm.receive_result("r1", "answer")
        assert sm.state == ResearchState.ready_to_finalize

        results = sm.finalize()
        assert results == {"r1": "answer"}
        assert sm.state == ResearchState.planning
        assert sm.in_flight_count == 0

    def test_replay_message_ignored(self) -> None:
        sm = ResearchStateMachine()
        sm.request_research("r1", "q1", "f", 100, now=0.0)

        assert sm.receive_result("r1", "answer", message_id="msg-1")
        # Replay with same message_id → ignored
        assert not sm.receive_result("r1", "answer", message_id="msg-1")

    def test_late_result_after_finalize_ignored(self) -> None:
        sm = ResearchStateMachine()
        sm.request_research("r1", "q1", "f", 100, now=0.0)
        sm.request_research("r2", "q2", "f", 100, now=0.0)
        sm.receive_result("r1", "a1")
        sm.receive_result("r2", "a2")
        sm.finalize()

        # Late result for r1 → ignored (not in-flight anymore)
        assert not sm.receive_result("r1", "late answer")

    def test_request_rejected_in_expired_state(self) -> None:
        sm = ResearchStateMachine()
        sm.force_expire()
        assert not sm.request_research("r1", "q1", "f", 100, now=0.0)

    def test_request_rejected_in_ready_to_finalize_state(self) -> None:
        sm = ResearchStateMachine()
        sm.request_research("r1", "q1", "f", 100, now=0.0)
        sm.receive_result("r1", "a")
        assert sm.state == ResearchState.ready_to_finalize
        assert not sm.request_research("r2", "q2", "f", 100, now=0.0)

    def test_reset_clears_everything(self) -> None:
        sm = ResearchStateMachine()
        sm.request_research("r1", "q1", "f", 100, now=0.0)
        sm.receive_result("r1", "a")
        sm.reset()
        assert sm.state == ResearchState.planning
        assert sm.in_flight_count == 0
        assert sm.total_dispatched == 0
        assert sm.results == {}


# ── PlannerConsumer Integration Tests ────────────────────────────────


@dataclass
class _FakePlanAction:
    plan_markdown: str


@dataclass
class _FakeOutput:
    plan_action: _FakePlanAction | None = None
    message: str = ""
    research_requests: list[dict[str, Any]] | None = None


@dataclass
class _FakeRunResult:
    output: _FakeOutput


class _FakePlannerAgent:
    """Mock planner that returns canned responses.

    Why a list of responses: the planner may be called multiple times
    in a research flow (initial call + finalize call). Sequential
    responses let us test the full cycle.
    """

    def __init__(self, responses: list[_FakeOutput]) -> None:
        self._responses = list(responses)
        self.call_count = 0
        self.prompts: list[str] = []

    async def run(self, prompt: str, deps: object | None = None) -> _FakeRunResult:
        self.prompts.append(prompt)
        self.call_count += 1
        if self._responses:
            output = self._responses.pop(0)
        else:
            output = _FakeOutput(message="no more responses")
        return _FakeRunResult(output=output)


@pytest.fixture
async def store(tmp_path: Any) -> DurableQueueStore:
    s = DurableQueueStore(str(tmp_path / "test.db"))
    await s.initialize()
    return s


@pytest.fixture
def router(store: DurableQueueStore) -> QueueRouter:
    return QueueRouter(store)


class TestPlannerConsumerResearchFlow:
    """Integration: PlannerConsumer + ResearchStateMachine."""

    @pytest.mark.asyncio
    async def test_plan_without_research_returns_immediately(
        self, store: DurableQueueStore, router: QueueRouter
    ) -> None:
        """No research requests → plan_result returned directly."""
        agent = _FakePlannerAgent(
            [
                _FakeOutput(
                    plan_action=_FakePlanAction("# My Plan"),
                    message="done",
                ),
            ]
        )
        consumer = PlannerConsumer(store, router, agent)

        msg = QueueMessage(
            message_kind="plan_request",
            sender="proxy",
            payload={"user_request": "do something"},
        )
        result = await consumer._process(msg)

        assert result is not None
        assert result.message_kind == "plan_result"
        assert result.payload["plan_markdown"] == "# My Plan"

    @pytest.mark.asyncio
    async def test_research_requests_dispatched_torouter(
        self, store: DurableQueueStore, router: QueueRouter
    ) -> None:
        """Planner requests research → research_request messages routed."""
        agent = _FakePlannerAgent(
            [
                _FakeOutput(
                    message="need research",
                    research_requests=[
                        {
                            "request_id": "r1",
                            "query": "what is X?",
                            "return_format": "short",
                            "max_tokens": 200,
                        },
                    ],
                ),
            ]
        )

        # Track what gets routed
        routed: list[QueueMessage] = []
        original_route = router.route

        async def capture_route(m: QueueMessage) -> None:
            routed.append(m)
            await original_route(m)

        router.route = capture_route  # type: ignore[assignment]

        consumer = PlannerConsumer(store, router, agent)
        msg = QueueMessage(
            message_kind="plan_request",
            sender="proxy",
            payload={"user_request": "complex task"},
        )
        result = await consumer._process(msg)

        # No plan_result yet — waiting for research
        assert result is None
        assert len(routed) == 1
        assert routed[0].message_kind == "research_request"
        assert routed[0].payload["query"] == "what is X?"
        assert routed[0].payload["research_mode"] is True
        assert consumer.research_sm.state == ResearchState.awaiting_research

    @pytest.mark.asyncio
    async def test_full_research_flow(self, store: DurableQueueStore, router: QueueRouter) -> None:
        """Full cycle: plan → research dispatched → result received → finalize."""
        agent = _FakePlannerAgent(
            [
                # First call: planner requests research
                _FakeOutput(
                    message="researching",
                    research_requests=[
                        {
                            "request_id": "r1",
                            "query": "stack info",
                            "return_format": "list",
                            "max_tokens": 200,
                        },
                    ],
                ),
                # Second call: planner finalizes with research context
                _FakeOutput(
                    plan_action=_FakePlanAction("# Final Plan with research"),
                    message="plan ready",
                ),
            ]
        )

        consumer = PlannerConsumer(store, router, agent)

        # Step 1: plan_request triggers research dispatch
        plan_msg = QueueMessage(
            message_kind="plan_request",
            sender="proxy",
            payload={"user_request": "deploy to staging"},
        )
        result = await consumer._process(plan_msg)
        assert result is None  # waiting for research

        # Step 2: research_result arrives
        research_msg = QueueMessage(
            message_kind="research_result",
            sender="executor",
            trace_id=plan_msg.trace_id,
            payload={"request_id": "r1", "result": "Stack: Python, SQLite, Redis"},
        )
        result = await consumer._process(research_msg)

        # Should finalize with plan_result
        assert result is not None
        assert result.message_kind == "plan_result"
        assert result.payload["plan_markdown"] == "# Final Plan with research"
        # Planner was called twice: once for initial, once for finalize
        assert agent.call_count == 2
        assert "research" in agent.prompts[1].lower()

    @pytest.mark.asyncio
    async def test_timeout_produces_partial_plan(
        self, store: DurableQueueStore, router: QueueRouter
    ) -> None:
        """Timeout with partial results → finalize with what we have."""
        # Use tiny timeout for testing
        sm = ResearchStateMachine(timeout_s=0.0)  # instant timeout

        agent = _FakePlannerAgent(
            [
                _FakeOutput(
                    message="researching",
                    research_requests=[
                        {
                            "request_id": "r1",
                            "query": "q1",
                            "return_format": "f",
                            "max_tokens": 100,
                        },
                        {
                            "request_id": "r2",
                            "query": "q2",
                            "return_format": "f",
                            "max_tokens": 100,
                        },
                    ],
                ),
                # Finalize call
                _FakeOutput(
                    plan_action=_FakePlanAction("# Partial Plan"),
                    message="partial",
                ),
            ]
        )

        consumer = PlannerConsumer(store, router, agent, research_sm=sm)

        # Dispatch research
        plan_msg = QueueMessage(
            message_kind="plan_request",
            sender="proxy",
            payload={"user_request": "do things"},
        )
        await consumer._process(plan_msg)

        # Only r1 responds; r2 will timeout
        research_msg = QueueMessage(
            message_kind="research_result",
            sender="executor",
            trace_id=plan_msg.trace_id,
            payload={"request_id": "r1", "result": "partial answer"},
        )
        result = await consumer._process(research_msg)

        # Should finalize with partial results (r2 timed out)
        assert result is not None
        assert result.message_kind == "plan_result"
        assert result.payload.get("partial_research") is True

    @pytest.mark.asyncio
    async def test_research_sm_exposed_on_consumer(
        self, store: DurableQueueStore, router: QueueRouter
    ) -> None:
        """Consumer exposes research_sm for introspection."""
        agent = _FakePlannerAgent([])
        sm = ResearchStateMachine(max_in_flight=2)
        consumer = PlannerConsumer(store, router, agent, research_sm=sm)
        assert consumer.research_sm is sm
        assert consumer.research_sm.max_in_flight == 2
