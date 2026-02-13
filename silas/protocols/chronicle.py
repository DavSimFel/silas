"""Chronicle store protocol for conversation persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from silas.models.context import ContextItem


@runtime_checkable
class ChronicleStore(Protocol):
    async def append(self, scope_id: str, item: ContextItem) -> None: ...

    async def get_recent(self, scope_id: str, limit: int) -> list[ContextItem]: ...

    async def prune_before(self, cutoff: datetime) -> int: ...


__all__ = ["ChronicleStore"]
