"""Base channel protocol for structural subtyping."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from silas.models.messages import ChannelMessage


@runtime_checkable
class ChannelAdapterCore(Protocol):
    @property
    def channel_name(self) -> str: ...

    async def listen(self) -> AsyncIterator[tuple[ChannelMessage, str]]: ...

    async def send(self, recipient_id: str, text: str, reply_to: str | None = None) -> None: ...

    async def send_stream_start(self, connection_id: str) -> None: ...

    async def send_stream_chunk(self, connection_id: str, text: str) -> None: ...

    async def send_stream_end(self, connection_id: str) -> None: ...


__all__ = ["ChannelAdapterCore"]
