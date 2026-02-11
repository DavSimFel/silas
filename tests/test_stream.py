"""Tests for the Stream turn processing (Phase 1a).

Tests cover: routing, taint classification, chronicle injection,
memory retrieval, context profile setting, and edge cases.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone

import pytest
from silas.core.stream import Stream
from silas.models.agents import InteractionMode, InteractionRegister, RouteDecision
from silas.models.context import ContextZone
from silas.models.gates import GateLane, GateResult
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


def _msg(text: str, sender_id: str = "owner") -> ChannelMessage:
    return ChannelMessage(
        channel="web",
        sender_id=sender_id,
        text=text,
        timestamp=datetime.now(timezone.utc),
    )


def _stream(
    channel: InMemoryChannel,
    turn_context,
    *,
    streaming_enabled: bool = False,
    chunk_size: int = 50,
    stream_chunk_delay_seconds: float = 0,
) -> Stream:
    return Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
        streaming_enabled=streaming_enabled,
        chunk_size=chunk_size,
        stream_chunk_delay_seconds=stream_chunk_delay_seconds,
    )


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for condition")


async def _stop_stream(stream: Stream, start_task: asyncio.Task[None]) -> None:
    start_task.cancel()
    with suppress(asyncio.CancelledError):
        await start_task

    inflight = list(stream._inflight_turn_tasks)
    for task in inflight:
        task.cancel()
    if inflight:
        await asyncio.gather(*inflight, return_exceptions=True)


class BlockingOutputGateRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, TaintLevel, str]] = []

    def evaluate(
        self,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
    ) -> tuple[str, list[GateResult]]:
        self.calls.append((response_text, response_taint, sender_id))
        return response_text, [
            GateResult(
                gate_name="block_all",
                lane=GateLane.policy,
                action="block",
                reason="blocked by test",
            )
        ]


class PlannerRouteModel:
    async def run(self, prompt: str) -> RunResult:
        del prompt
        return RunResult(
            output=RouteDecision(
                route="planner",
                reason="needs planning",
                response=None,
                interaction_register=InteractionRegister.execution,
                interaction_mode=InteractionMode.default_and_offer,
                context_profile="planning",
            )
        )


@pytest.mark.asyncio
async def test_process_turn_returns_echo_response(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    stream = _stream(channel, turn_context)
    result = await stream._process_turn(_msg("hello world"))
    assert result == "echo: hello world"


@pytest.mark.asyncio
async def test_response_sent_to_channel(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("test"))
    assert len(channel.outgoing) == 1
    assert channel.outgoing[0]["text"] == "echo: test"
    assert channel.outgoing[0]["recipient_id"] == "owner"


@pytest.mark.asyncio
async def test_response_streamed_when_enabled(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    stream = _stream(
        channel,
        turn_context,
        streaming_enabled=True,
        chunk_size=5,
        stream_chunk_delay_seconds=0,
    )

    await stream._process_turn(_msg("stream me"))

    assert channel.outgoing == []
    assert channel.stream_events[0]["type"] == "stream_start"
    assert channel.stream_events[-1]["type"] == "stream_end"
    chunks = [event["text"] for event in channel.stream_events if event["type"] == "stream_chunk"]
    assert "".join(chunks) == "echo: stream me"
    assert all(0 < len(chunk) <= 5 for chunk in chunks)


def test_chunk_text_respects_chunk_size(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    stream = _stream(channel, turn_context, chunk_size=4)
    assert stream._chunk_text("abcdefghij") == ["abcd", "efgh", "ij"]


@pytest.mark.asyncio
async def test_same_connection_messages_queue_while_streaming(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    stream = _stream(
        channel,
        turn_context,
        streaming_enabled=True,
        chunk_size=4,
        stream_chunk_delay_seconds=0.01,
    )
    start_task = asyncio.create_task(stream.start())

    await channel.push_message("first", scope_id="conn-1")
    await channel.push_message("second", scope_id="conn-1")

    await _wait_until(
        lambda: sum(1 for event in channel.stream_events if event["type"] == "stream_end") >= 2
    )
    await _stop_stream(stream, start_task)

    types = [event["type"] for event in channel.stream_events]
    first_end = types.index("stream_end")
    second_start = types.index("stream_start", first_end + 1)
    assert first_end < second_start


@pytest.mark.asyncio
async def test_streaming_does_not_block_other_connections(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    stream = _stream(
        channel,
        turn_context,
        streaming_enabled=True,
        chunk_size=2,
        stream_chunk_delay_seconds=0.02,
    )
    start_task = asyncio.create_task(stream.start())

    await channel.push_message("abcdefghijklmnopqrstuvwxyz", scope_id="conn-1")
    await channel.push_message("ok", scope_id="conn-2")

    await _wait_until(
        lambda: len(
            [
                event
                for event in channel.stream_events
                if event["type"] == "stream_end"
                and event["connection_id"] in {"conn-1", "conn-2"}
            ]
        )
        >= 2
    )
    await _stop_stream(stream, start_task)

    conn1_end = next(
        index
        for index, event in enumerate(channel.stream_events)
        if event["type"] == "stream_end" and event["connection_id"] == "conn-1"
    )
    conn2_end = next(
        index
        for index, event in enumerate(channel.stream_events)
        if event["type"] == "stream_end" and event["connection_id"] == "conn-2"
    )
    assert conn2_end < conn1_end


@pytest.mark.asyncio
async def test_chronicle_zone_populated(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
) -> None:
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hello"))
    chronicle = context_manager.get_zone("owner", ContextZone.chronicle)
    assert len(chronicle) == 2  # user message + agent response
    assert "[owner]" in chronicle[0].content
    assert "hello" in chronicle[0].content
    assert "Silas:" in chronicle[1].content


@pytest.mark.asyncio
async def test_owner_taint_classification(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
) -> None:
    """Owner sender_id should produce owner taint."""
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hi", sender_id="owner"))
    chronicle = context_manager.get_zone("owner", ContextZone.chronicle)
    assert chronicle[0].taint == TaintLevel.owner


@pytest.mark.asyncio
async def test_external_taint_classification(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
) -> None:
    """Non-owner sender should produce external taint."""
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hi", sender_id="stranger"))
    chronicle = context_manager.get_zone("owner", ContextZone.chronicle)
    assert chronicle[0].taint == TaintLevel.external


@pytest.mark.asyncio
async def test_memory_retrieval_injected(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
    memory_store: InMemoryMemoryStore,
) -> None:
    """Matching memory items should appear in the memory zone."""
    await memory_store.store(sample_memory_item("m1", "hello world context"))
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hello"))
    memory_zone = context_manager.get_zone("owner", ContextZone.memory)
    assert len(memory_zone) == 1
    assert memory_zone[0].content == "hello world context"


@pytest.mark.asyncio
async def test_no_memory_match_empty_zone(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
    memory_store: InMemoryMemoryStore,
) -> None:
    """No matching memories should leave memory zone empty."""
    await memory_store.store(sample_memory_item("m1", "completely unrelated"))
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("xyz123"))
    memory_zone = context_manager.get_zone("owner", ContextZone.memory)
    assert len(memory_zone) == 0


@pytest.mark.asyncio
async def test_auto_retrieval_deduplicates_keyword_and_entity_matches(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
    memory_store: InMemoryMemoryStore,
) -> None:
    await memory_store.store(
        MemoryItem(
            memory_id="m-entity-1",
            content="notes for @alice",
            memory_type=MemoryType.entity,
            taint=TaintLevel.owner,
            source_kind="test",
            entity_refs=["alice"],
        )
    )

    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("@alice"))

    memory_zone = context_manager.get_zone("owner", ContextZone.memory)
    injected_ids = [item.ctx_id for item in memory_zone]
    assert injected_ids.count("memory:m-entity-1") == 1
    assert memory_store.incremented_ids.count("m-entity-1") == 1


@pytest.mark.asyncio
async def test_process_turn_stores_raw_memory_with_session_id(
    channel: InMemoryChannel,
    turn_context,
    memory_store: InMemoryMemoryStore,
) -> None:
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("persist this turn"))

    raw_items = [
        item for item in memory_store.items.values()
        if item.source_kind == "conversation_raw"
    ]
    assert len(raw_items) == 1
    assert raw_items[0].session_id == stream.session_id
    assert raw_items[0].session_id is not None


@pytest.mark.asyncio
async def test_rehydrate_loads_recent_memories_for_current_session(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
    memory_store: InMemoryMemoryStore,
) -> None:
    await memory_store.store(
        MemoryItem(
            memory_id="rehydrate-sess-1",
            content="session memory one",
            memory_type=MemoryType.fact,
            taint=TaintLevel.owner,
            source_kind="test",
            session_id="session-a",
        )
    )
    await memory_store.store(
        MemoryItem(
            memory_id="rehydrate-sess-2",
            content="session memory two",
            memory_type=MemoryType.fact,
            taint=TaintLevel.owner,
            source_kind="test",
            session_id="session-a",
        )
    )
    await memory_store.store(
        MemoryItem(
            memory_id="rehydrate-other",
            content="other session memory",
            memory_type=MemoryType.fact,
            taint=TaintLevel.owner,
            source_kind="test",
            session_id="session-b",
        )
    )

    stream = _stream(channel, turn_context)
    stream.session_id = "session-a"
    await stream._rehydrate()

    memory_zone = context_manager.get_zone("owner", ContextZone.memory)
    memory_ids = {item.ctx_id for item in memory_zone}
    assert "memory:session:rehydrate-sess-1" in memory_ids
    assert "memory:session:rehydrate-sess-2" in memory_ids
    assert "memory:session:rehydrate-other" not in memory_ids


@pytest.mark.asyncio
async def test_turn_number_increments(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    stream = _stream(channel, turn_context)
    assert turn_context.turn_number == 0
    await stream._process_turn(_msg("first"))
    assert turn_context.turn_number == 1
    await stream._process_turn(_msg("second"))
    assert turn_context.turn_number == 2


@pytest.mark.asyncio
async def test_context_profile_set_after_routing(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
) -> None:
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hello"))
    assert context_manager.profile_by_scope.get("owner") == "conversation"


@pytest.mark.asyncio
async def test_audit_events_logged(
    channel: InMemoryChannel,
    turn_context,
    audit_log: InMemoryAuditLog,
) -> None:
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hi"))
    event_names = [e["event"] for e in audit_log.events]
    assert "turn_processed" in event_names


@pytest.mark.asyncio
async def test_output_gate_block_sanitizes_response(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    gate_runner = BlockingOutputGateRunner()
    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
        output_gate_runner=gate_runner,
        streaming_enabled=False,
    )

    result = await stream._process_turn(_msg("hello", sender_id="stranger"))

    assert result == "I cannot share that"
    assert channel.outgoing[0]["text"] == "I cannot share that"
    assert gate_runner.calls == [("echo: hello", TaintLevel.external, "stranger")]


@pytest.mark.asyncio
async def test_planner_route_response_runs_through_output_gates(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    turn_context.proxy = PlannerRouteModel()
    gate_runner = BlockingOutputGateRunner()
    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
        output_gate_runner=gate_runner,
        streaming_enabled=False,
    )

    result = await stream._process_turn(_msg("build a 5-step plan"))

    assert gate_runner.calls[0][0] == stream._planner_stub_response()
    assert result == "I cannot share that"
    assert channel.outgoing[0]["text"] == "I cannot share that"


@pytest.mark.asyncio
async def test_no_proxy_raises(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    """Stream must raise if proxy is not set."""
    turn_context.proxy = None
    stream = _stream(channel, turn_context)
    with pytest.raises(RuntimeError, match="proxy"):
        await stream._process_turn(_msg("hello"))
