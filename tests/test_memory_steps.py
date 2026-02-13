"""Tests for memory steps 9, 10, and 11.5 in the turn pipeline.

Covers: query execution, taint filtering, op gating, op execution,
and raw output ingest.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from silas.core.stream import Stream
from silas.core.turn_context import TurnContext
from silas.models.agents import (
    AgentResponse,
    InteractionMode,
    InteractionRegister,
    MemoryOp,
    MemoryOpType,
    MemoryQuery,
    MemoryQueryStrategy,
    RouteDecision,
)
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import ChannelMessage, TaintLevel

from tests.fakes import (
    InMemoryAuditLog,
    InMemoryChannel,
    InMemoryContextManager,
    InMemoryMemoryStore,
    RunResult,
    sample_memory_item,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(text: str, sender_id: str = "owner") -> ChannelMessage:
    return ChannelMessage(
        channel="web",
        sender_id=sender_id,
        text=text,
        timestamp=datetime.now(UTC),
    )


def _stream(
    channel: InMemoryChannel,
    turn_context: TurnContext,
) -> Stream:
    return Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
    )


class MemoryQueryModel:
    """Proxy model that emits memory_queries in its response."""

    def __init__(self, queries: list[MemoryQuery]) -> None:
        self._queries = queries

    async def run(self, prompt: str) -> RunResult:
        return RunResult(
            output=RouteDecision(
                route="direct",
                reason="test",
                response=AgentResponse(
                    message="response with queries",
                    memory_queries=self._queries,
                    needs_approval=False,
                ),
                interaction_register=InteractionRegister.status,
                interaction_mode=InteractionMode.default_and_offer,
                context_profile="conversation",
            ),
        )


class MemoryOpModel:
    """Proxy model that emits memory_ops in its response."""

    def __init__(self, ops: list[MemoryOp]) -> None:
        self._ops = ops

    async def run(self, prompt: str) -> RunResult:
        return RunResult(
            output=RouteDecision(
                route="direct",
                reason="test",
                response=AgentResponse(
                    message="response with ops",
                    memory_ops=self._ops,
                    needs_approval=False,
                ),
                interaction_register=InteractionRegister.status,
                interaction_mode=InteractionMode.default_and_offer,
                context_profile="conversation",
            ),
        )


class PlainModel:
    """Proxy model that returns a plain response (no queries/ops)."""

    def __init__(self, message: str = "plain response") -> None:
        self._message = message

    async def run(self, prompt: str) -> RunResult:
        return RunResult(
            output=RouteDecision(
                route="direct",
                reason="test",
                response=AgentResponse(
                    message=self._message,
                    needs_approval=False,
                ),
                interaction_register=InteractionRegister.status,
                interaction_mode=InteractionMode.default_and_offer,
                context_profile="conversation",
            ),
        )


# ---------------------------------------------------------------------------
# Step 9 — Memory query execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step9_memory_queries_executed() -> None:
    """Memory queries from the agent response should hit the store and be audited."""
    store = InMemoryMemoryStore()
    # Seed a memory that matches the keyword query.
    await store.store(sample_memory_item("mem-1", "the capital of France is Paris"))

    queries = [MemoryQuery(strategy=MemoryQueryStrategy.keyword, query="France")]
    model = MemoryQueryModel(queries)
    audit = InMemoryAuditLog()
    channel = InMemoryChannel()
    tc = TurnContext(
        scope_id="owner",
        context_manager=InMemoryContextManager(),
        memory_store=store,
        proxy=model,
        audit=audit,
    )
    stream = _stream(channel, tc)

    await stream._process_turn(_msg("hello"), "conn-1")

    # Verify the query was executed — audit should contain the event.
    query_events = [e for e in audit.events if e["event"] == "memory_query_executed"]
    assert len(query_events) == 1
    assert query_events[0]["data"]["result_count"] >= 1


@pytest.mark.asyncio
async def test_step9_taint_filters_owner_memories() -> None:
    """External-tainted requests must not receive owner-tainted memories."""
    store = InMemoryMemoryStore()
    # Owner-tainted memory should be filtered out.
    owner_mem = sample_memory_item("mem-owner", "secret owner data about cats")
    await store.store(owner_mem)

    # Also store an external-tainted memory that should pass through.
    ext_mem = MemoryItem(
        memory_id="mem-ext",
        content="public info about cats",
        memory_type=MemoryType.fact,
        taint=TaintLevel.external,
        source_kind="conversation_raw",
    )
    await store.store(ext_mem)

    queries = [MemoryQuery(strategy=MemoryQueryStrategy.keyword, query="cats")]
    model = MemoryQueryModel(queries)
    audit = InMemoryAuditLog()
    channel = InMemoryChannel()
    tc = TurnContext(
        scope_id="owner",
        context_manager=InMemoryContextManager(),
        memory_store=store,
        proxy=model,
        audit=audit,
    )
    stream = _stream(channel, tc)
    # External sender triggers external taint classification.
    stream.owner_id = "owner"

    await stream._process_turn(_msg("tell me about cats", sender_id="stranger"), "conn-1")

    query_events = [e for e in audit.events if e["event"] == "memory_query_executed"]
    assert len(query_events) == 1
    # Owner-tainted memories must be excluded; step 3.5's raw ingest also
    # matches "cats" with external taint, so >=1 non-owner results pass.
    result_count = query_events[0]["data"]["result_count"]
    assert result_count >= 1
    # The seeded owner memory must NOT be in the results — verify the count
    # is less than total "cats" matches (which includes the owner one).
    all_cats = await store.search_keyword("cats", limit=10)
    owner_cats = [i for i in all_cats if i.taint == TaintLevel.owner]
    assert len(owner_cats) >= 1, "sanity: owner memory exists"
    assert result_count == len(all_cats) - len(owner_cats)


# ---------------------------------------------------------------------------
# Step 10 — Memory op execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step10_memory_ops_executed() -> None:
    """Memory store ops from the agent are executed when owner-tainted."""
    store = InMemoryMemoryStore()
    ops = [
        MemoryOp(op=MemoryOpType.store, content="remember this fact", memory_type=MemoryType.fact),
    ]
    model = MemoryOpModel(ops)
    audit = InMemoryAuditLog()
    channel = InMemoryChannel()
    tc = TurnContext(
        scope_id="owner",
        context_manager=InMemoryContextManager(),
        memory_store=store,
        proxy=model,
        audit=audit,
    )
    stream = _stream(channel, tc)

    await stream._process_turn(_msg("store something"), "conn-1")

    op_events = [e for e in audit.events if e["event"] == "memory_op_executed"]
    assert len(op_events) == 1
    # The fact should be in the store.
    agent_items = [i for i in store.items.values() if i.source_kind == "agent_memory_op"]
    assert len(agent_items) == 1
    assert agent_items[0].content == "remember this fact"


@pytest.mark.asyncio
async def test_step10_memory_ops_blocked_for_external() -> None:
    """External-tainted requests must not be allowed to write memories."""
    store = InMemoryMemoryStore()
    ops = [
        MemoryOp(op=MemoryOpType.store, content="injected content", memory_type=MemoryType.fact),
    ]
    model = MemoryOpModel(ops)
    audit = InMemoryAuditLog()
    channel = InMemoryChannel()
    tc = TurnContext(
        scope_id="owner",
        context_manager=InMemoryContextManager(),
        memory_store=store,
        proxy=model,
        audit=audit,
    )
    stream = _stream(channel, tc)
    stream.owner_id = "owner"

    await stream._process_turn(_msg("inject this", sender_id="attacker"), "conn-1")

    blocked_events = [e for e in audit.events if e["event"] == "memory_ops_blocked"]
    assert len(blocked_events) == 1
    # Nothing should have been stored.
    agent_items = [i for i in store.items.values() if i.source_kind == "agent_memory_op"]
    assert len(agent_items) == 0


@pytest.mark.asyncio
async def test_step10_delete_op() -> None:
    """Delete ops should remove items from the store."""
    store = InMemoryMemoryStore()
    await store.store(sample_memory_item("mem-del", "to be deleted"))
    assert await store.get("mem-del") is not None

    ops = [MemoryOp(op=MemoryOpType.delete, memory_id="mem-del")]
    model = MemoryOpModel(ops)
    audit = InMemoryAuditLog()
    channel = InMemoryChannel()
    tc = TurnContext(
        scope_id="owner",
        context_manager=InMemoryContextManager(),
        memory_store=store,
        proxy=model,
        audit=audit,
    )
    stream = _stream(channel, tc)

    await stream._process_turn(_msg("delete that"), "conn-1")

    assert await store.get("mem-del") is None


# ---------------------------------------------------------------------------
# Step 11.5 — Raw output ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step11_5_output_ingested_as_raw_memory() -> None:
    """The agent's response text should be stored as a raw memory item."""
    store = InMemoryMemoryStore()
    model = PlainModel(message="I will remember this response")
    audit = InMemoryAuditLog()
    channel = InMemoryChannel()
    tc = TurnContext(
        scope_id="owner",
        context_manager=InMemoryContextManager(),
        memory_store=store,
        proxy=model,
        audit=audit,
    )
    stream = _stream(channel, tc)

    await stream._process_turn(_msg("say something"), "conn-1")

    # There should be raw items: one for input (step 3.5) and one for output (step 11.5).
    raw_items = [i for i in store.items.values() if i.source_kind == "conversation_raw"]
    # At least the output should be there.
    output_raws = [i for i in raw_items if "I will remember this response" in i.content]
    assert len(output_raws) == 1
    assert output_raws[0].taint == TaintLevel.owner
