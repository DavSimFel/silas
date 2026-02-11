from __future__ import annotations

from datetime import datetime, timezone

import pytest
from silas.core.stream import Stream
from silas.models.context import ContextZone
from silas.models.messages import ChannelMessage
from tests.fakes import (
    InMemoryChannel,
    InMemoryContextManager,
    InMemoryMemoryStore,
    sample_memory_item,
)


@pytest.mark.asyncio
async def test_stream_process_turn_routes_proxy_and_sends_response(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
    memory_store: InMemoryMemoryStore,
) -> None:
    await memory_store.store(sample_memory_item("m1", "echo hello world"))

    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
    )

    message = ChannelMessage(
        channel="web",
        sender_id="owner",
        text="hello world",
        timestamp=datetime.now(timezone.utc),
    )

    result = await stream._process_turn(message, connection_id="owner")

    assert result == "echo: hello world"
    assert channel.outgoing[-1]["text"] == "echo: hello world"

    chronicle = context_manager.get_zone("owner", ContextZone.chronicle)
    memory = context_manager.get_zone("owner", ContextZone.memory)
    assert len(chronicle) == 1
    assert len(memory) == 1
