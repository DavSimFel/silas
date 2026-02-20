"""Concurrent turn isolation tests.

Proves that concurrent asyncio tasks don't leak state between each other.
This is critical because the runtime processes multiple connections via
asyncio.gather — if contextvars, memory stores, or queue routing leak
across tasks, we get silent correctness bugs.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from silas.context.manager import LiveContextManager
from silas.core.token_counter import HeuristicTokenCounter
from silas.models.context import ContextItem, ContextZone, TokenBudget
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import TaintLevel
from silas.execution.bridge import QueueBridge
from silas.execution.orchestrator import QueueOrchestrator
from silas.execution.router import QueueRouter
from silas.execution.queue_store import DurableQueueStore
from silas.execution.queue_types import QueueMessage
from silas.gates.taint import TaintTracker

from tests.fakes import InMemoryMemoryStore

# ── Helpers ────────────────────────────────────────────────────────────


def _make_memory_item(content: str, session_id: str) -> MemoryItem:
    """Build a minimal MemoryItem for isolation tests."""
    return MemoryItem(
        memory_id=str(uuid.uuid4()),
        content=content,
        memory_type=MemoryType.episode,
        session_id=session_id,
        source_kind="test",
    )


def _make_context_item(
    content: str,
    zone: ContextZone = ContextZone.chronicle,
    turn: int = 0,
    tokens: int = 100,
) -> ContextItem:
    return ContextItem(
        ctx_id=str(uuid.uuid4()),
        content=content,
        zone=zone,
        source="test",
        turn_number=turn,
        token_count=tokens,
        kind="message",
    )


async def _build_queue_infra(
    tmp_path: str,
) -> tuple[DurableQueueStore, QueueRouter, QueueOrchestrator, QueueBridge]:
    """Spin up real queue infrastructure with file-backed SQLite.

    Can't use :memory: because aiosqlite opens a new connection per call,
    and each :memory: connection gets a separate database.
    """
    store = DurableQueueStore(db_path=tmp_path)
    await store.initialize()
    router = QueueRouter(store)
    orch = QueueOrchestrator(store, router, [])
    bridge = QueueBridge(orchestrator=orch, router=router, store=store)
    return store, router, orch, bridge


# ── Test 1: Taint Isolation ───────────────────────────────────────────


class TestTaintIsolation:
    """Verify contextvars-backed taint tracker doesn't leak between concurrent tasks.

    The TaintTracker uses a module-level ContextVar. Without proper asyncio
    task isolation, Turn A calling on_tool_output("web_search") would
    ratchet the taint for Turn B too.
    """

    @pytest.mark.asyncio
    async def test_external_taint_does_not_leak_to_owner_turn(self) -> None:
        # Each task gets its own contextvars copy because asyncio.create_task
        # copies the parent context. We verify that copy semantics hold.
        results: dict[str, TaintLevel] = {}
        barrier = asyncio.Barrier(2)

        async def turn_a() -> None:
            """Simulates a turn that uses an external tool."""
            tracker = TaintTracker()
            tracker.reset()
            await barrier.wait()  # sync so both tasks run concurrently
            # External tool ratchets taint to external
            tracker.on_tool_output("web_search")
            # Yield control — gives turn_b a chance to be affected if leaking
            await asyncio.sleep(0)
            results["a"] = tracker.get_current_taint()

        async def turn_b() -> None:
            """Simulates a turn using only internal tools."""
            tracker = TaintTracker()
            tracker.reset()
            await barrier.wait()
            # Internal tool should NOT escalate taint
            tracker.on_tool_output("memory_recall")
            await asyncio.sleep(0)
            results["b"] = tracker.get_current_taint()

        await asyncio.gather(turn_a(), turn_b())

        assert results["a"] == TaintLevel.external, "Turn A should have external taint"
        assert results["b"] == TaintLevel.owner, "Turn B must stay owner — taint leaked from A"

    @pytest.mark.asyncio
    async def test_auth_taint_isolated_from_owner(self) -> None:
        """Auth-level taint in one turn shouldn't affect a parallel owner turn."""
        results: dict[str, TaintLevel] = {}

        async def turn_auth() -> None:
            tracker = TaintTracker()
            tracker.reset()
            tracker.on_tool_output("calendar_read")
            results["auth"] = tracker.get_current_taint()

        async def turn_owner() -> None:
            tracker = TaintTracker()
            tracker.reset()
            # Slight delay so auth turn's on_tool_output runs first
            await asyncio.sleep(0.01)
            results["owner"] = tracker.get_current_taint()

        await asyncio.gather(turn_auth(), turn_owner())

        assert results["auth"] == TaintLevel.auth
        assert results["owner"] == TaintLevel.owner


# ── Test 2: Memory Isolation ──────────────────────────────────────────


class TestMemoryIsolation:
    """Each turn's memory writes must be scoped — no cross-user leakage.

    Uses separate InMemoryMemoryStore instances per user to model the
    real pattern where each scope_id gets its own store namespace.
    """

    @pytest.mark.asyncio
    async def test_concurrent_writes_stay_scoped(self) -> None:
        # Separate stores per user — mirrors real per-scope_id isolation
        alice_store = InMemoryMemoryStore()
        bob_store = InMemoryMemoryStore()

        async def alice_turn() -> None:
            for i in range(5):
                await alice_store.store(
                    _make_memory_item(f"alice-memory-{i}", session_id="alice-session")
                )
                await asyncio.sleep(0)  # interleave with bob

        async def bob_turn() -> None:
            for i in range(5):
                await bob_store.store(
                    _make_memory_item(f"bob-memory-{i}", session_id="bob-session")
                )
                await asyncio.sleep(0)

        await asyncio.gather(alice_turn(), bob_turn())

        alice_items = await alice_store.list_recent(limit=100)
        bob_items = await bob_store.list_recent(limit=100)

        assert len(alice_items) == 5
        assert len(bob_items) == 5
        # No cross-contamination — alice's store has none of bob's content
        assert all("alice" in item.content for item in alice_items)
        assert all("bob" in item.content for item in bob_items)

    @pytest.mark.asyncio
    async def test_shared_store_session_isolation(self) -> None:
        """Even with a shared store, session_id filtering isolates retrieval."""
        shared_store = InMemoryMemoryStore()

        async def alice_writes() -> None:
            for i in range(3):
                await shared_store.store(
                    _make_memory_item(f"alice-thought-{i}", session_id="session-alice")
                )
                await asyncio.sleep(0)

        async def bob_writes() -> None:
            for i in range(3):
                await shared_store.store(
                    _make_memory_item(f"bob-thought-{i}", session_id="session-bob")
                )
                await asyncio.sleep(0)

        await asyncio.gather(alice_writes(), bob_writes())

        alice_results = await shared_store.search_session("session-alice")
        bob_results = await shared_store.search_session("session-bob")

        assert len(alice_results) == 3
        assert len(bob_results) == 3
        assert all("alice" in r.content for r in alice_results)
        assert all("bob" in r.content for r in bob_results)


# ── Test 3: Context Window Isolation ──────────────────────────────────


class TestContextWindowIsolation:
    """Context eviction in one scope must not affect another scope's window.

    LiveContextManager uses per-scope_id storage (by_scope dict), so
    eviction in scope "A" should leave scope "B" completely untouched.
    """

    @pytest.mark.asyncio
    async def test_eviction_in_one_scope_does_not_affect_another(self) -> None:
        # Tiny budget forces eviction when scope_a fills up
        budget = TokenBudget(
            total=500,
            system_max=50,
            default_profile="conversation",
            profiles={
                "conversation": _make_profile("conversation", 0.45, 0.20, 0.15),
            },
        )
        counter = HeuristicTokenCounter()
        cm = LiveContextManager(token_budget=budget, token_counter=counter, use_scorer=False)

        scope_a = "long-conversation"
        scope_b = "fresh-conversation"

        async def fill_scope_a() -> None:
            """Load scope_a past budget so eviction triggers."""
            for i in range(20):
                cm.add(scope_a, _make_context_item(f"message-{i} " * 20, turn=i, tokens=50))
                await asyncio.sleep(0)
            # Trigger eviction
            cm.enforce_budget(scope_a, turn_number=20, current_goal=None)

        async def fill_scope_b() -> None:
            """Scope B gets just 2 items — well under budget."""
            cm.add(scope_b, _make_context_item("hello", turn=0, tokens=10))
            cm.add(scope_b, _make_context_item("how are you", turn=1, tokens=10))
            await asyncio.sleep(0)

        await asyncio.gather(fill_scope_a(), fill_scope_b())

        # Scope B's items must survive scope A's eviction
        scope_b_items = cm.by_scope.get(scope_b, [])
        assert len(scope_b_items) == 2, (
            f"Scope B lost items during scope A's eviction: {len(scope_b_items)} != 2"
        )

        # Scope A should have fewer items after eviction
        scope_a_items = cm.by_scope.get(scope_a, [])
        assert len(scope_a_items) < 20, "Eviction should have removed some items from scope A"

    @pytest.mark.asyncio
    async def test_render_isolated_between_scopes(self) -> None:
        """Rendering one scope's window doesn't include the other's content."""
        budget = TokenBudget(total=10000, system_max=100, default_profile="conversation")
        counter = HeuristicTokenCounter()
        cm = LiveContextManager(token_budget=budget, token_counter=counter)

        cm.add("scope-x", _make_context_item("secret-x-data", turn=0))
        cm.add("scope-y", _make_context_item("secret-y-data", turn=0))

        render_x = cm.render("scope-x", turn_number=1)
        render_y = cm.render("scope-y", turn_number=1)

        assert "secret-x-data" in render_x
        assert "secret-y-data" not in render_x
        assert "secret-y-data" in render_y
        assert "secret-x-data" not in render_y


# ── Test 4: Queue Routing Isolation ───────────────────────────────────


class TestQueueRoutingIsolation:
    """Concurrent dispatch_turn calls must route independently.

    Each call gets its own trace_id; routing decisions and responses
    must never cross-contaminate between traces.
    """

    @pytest.mark.asyncio
    async def test_concurrent_dispatches_get_independent_routing(self, tmp_path: object) -> None:
        db_path = str(tmp_path / "queue_routing.db")  # type: ignore[operator]
        store, _router, _orch, bridge = await _build_queue_infra(db_path)

        trace_ids = [str(uuid.uuid4()) for _ in range(3)]
        messages = ["hello from trace-0", "hello from trace-1", "hello from trace-2"]

        # Dispatch all three concurrently
        await asyncio.gather(
            *(
                bridge.dispatch_turn(msg, trace_id=tid)
                for msg, tid in zip(messages, trace_ids, strict=True)
            )
        )

        # All three should land in proxy_queue with correct trace_ids
        leased: list[QueueMessage] = []
        for _ in range(3):
            msg = await store.lease(queue_name="proxy_queue", lease_duration_s=5)
            assert msg is not None
            leased.append(msg)

        leased_trace_ids = {m.trace_id for m in leased}
        assert leased_trace_ids == set(trace_ids), (
            "Each dispatch must produce a message with its own trace_id"
        )

        # Verify payloads match their trace_ids
        for msg in leased:
            idx = trace_ids.index(msg.trace_id)
            assert msg.payload["text"] == messages[idx]

    @pytest.mark.asyncio
    async def test_collect_response_matches_correct_trace(self, tmp_path: object) -> None:
        """collect_response must only return the response for its own trace_id."""
        db_path = str(tmp_path / "queue_collect.db")  # type: ignore[operator]
        _store, router, _orch, bridge = await _build_queue_infra(db_path)

        trace_a = str(uuid.uuid4())
        trace_b = str(uuid.uuid4())

        # Enqueue responses for both traces (simulating agent output)
        for tid, text in [(trace_a, "response-a"), (trace_b, "response-b")]:
            resp = QueueMessage(
                message_kind="agent_response",
                sender="proxy",
                trace_id=tid,
                payload={"text": text},
            )
            await router.route(resp)

        # Collect in reverse order — trace_b first
        result_b = await bridge.collect_response(trace_b, timeout_s=2.0)
        result_a = await bridge.collect_response(trace_a, timeout_s=2.0)

        assert result_b is not None
        assert result_b.payload["text"] == "response-b"
        assert result_a is not None
        assert result_a.payload["text"] == "response-a"


# ── Test 5: Error Isolation ───────────────────────────────────────────


class TestErrorIsolation:
    """A failing turn must not poison a concurrent healthy turn.

    asyncio.gather with return_exceptions=True is the standard pattern,
    but we also need to verify that shared resources (taint, memory)
    remain consistent after a partial failure.
    """

    @pytest.mark.asyncio
    async def test_exception_in_one_turn_does_not_kill_another(self) -> None:
        results: dict[str, str] = {}

        async def failing_turn() -> str:
            await asyncio.sleep(0.01)  # slight delay so both are in-flight
            raise RuntimeError("simulated mid-turn crash")

        async def healthy_turn() -> str:
            await asyncio.sleep(0.02)  # runs slightly after failure
            results["healthy"] = "completed"
            return "success"

        outcomes = await asyncio.gather(failing_turn(), healthy_turn(), return_exceptions=True)

        # Turn A failed
        assert isinstance(outcomes[0], RuntimeError)
        # Turn B completed despite A's failure
        assert outcomes[1] == "success"
        assert results["healthy"] == "completed"

    @pytest.mark.asyncio
    async def test_taint_consistent_after_partial_failure(self) -> None:
        """Taint state in the surviving turn must be correct after a sibling crashes."""
        results: dict[str, TaintLevel] = {}

        async def crashing_turn() -> None:
            tracker = TaintTracker()
            tracker.reset()
            tracker.on_tool_output("web_search")  # escalate to external
            raise RuntimeError("boom")

        async def surviving_turn() -> None:
            tracker = TaintTracker()
            tracker.reset()
            # Small delay so crashing_turn runs and fails first
            await asyncio.sleep(0.02)
            tracker.on_tool_output("memory_recall")
            results["survivor"] = tracker.get_current_taint()

        outcomes = await asyncio.gather(crashing_turn(), surviving_turn(), return_exceptions=True)

        assert isinstance(outcomes[0], RuntimeError)
        # The surviving turn's taint must be unaffected by the crash
        assert results["survivor"] == TaintLevel.owner

    @pytest.mark.asyncio
    async def test_memory_writes_survive_sibling_failure(self) -> None:
        """Memory written by the healthy turn persists even if sibling crashes."""
        store = InMemoryMemoryStore()

        async def crashing_writer() -> None:
            await store.store(_make_memory_item("before-crash", session_id="crash"))
            raise RuntimeError("crash mid-write")

        async def healthy_writer() -> None:
            await asyncio.sleep(0.01)
            await store.store(_make_memory_item("healthy-write", session_id="ok"))

        await asyncio.gather(crashing_writer(), healthy_writer(), return_exceptions=True)

        # Healthy turn's write must survive
        ok_items = await store.search_session("ok")
        assert len(ok_items) == 1
        assert "healthy-write" in ok_items[0].content

        # Crashing turn's pre-crash write also persists (no rollback)
        crash_items = await store.search_session("crash")
        assert len(crash_items) == 1


# ── Profile helper ────────────────────────────────────────────────────


def _make_profile(
    name: str,
    chronicle_pct: float,
    memory_pct: float,
    workspace_pct: float,
) -> object:
    """Build a ContextProfile without importing the model at module level."""
    from silas.models.context import ContextProfile

    return ContextProfile(
        name=name,
        chronicle_pct=chronicle_pct,
        memory_pct=memory_pct,
        workspace_pct=workspace_pct,
    )
