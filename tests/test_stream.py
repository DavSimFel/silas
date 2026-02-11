"""Tests for the Stream turn processing (Phase 1a).

Tests cover: routing, taint classification, chronicle injection,
memory retrieval, context profile setting, and edge cases.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from silas.core.stream import Stream
from silas.models.context import ContextZone
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import ChannelMessage, TaintLevel

from tests.fakes import (
    InMemoryAuditLog,
    InMemoryChannel,
    InMemoryContextManager,
    InMemoryMemoryStore,
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
) -> Stream:
    return Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
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
async def test_no_proxy_raises(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    """Stream must raise if proxy is not set."""
    turn_context.proxy = None
    stream = _stream(channel, turn_context)
    with pytest.raises(RuntimeError, match="proxy"):
        await stream._process_turn(_msg("hello"))
