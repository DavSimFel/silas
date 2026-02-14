from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from silas.models.agents import MemoryQuery
from silas.models.memory import MemoryItem, MemoryType


@runtime_checkable
class MemoryStore(Protocol):
    async def store(self, item: MemoryItem) -> str: ...

    async def get(self, memory_id: str) -> MemoryItem | None: ...

    async def update(self, memory_id: str, **kwargs: object) -> None: ...

    async def delete(self, memory_id: str) -> None: ...

    async def search_keyword(self, query: str, limit: int) -> list[MemoryItem]: ...

    async def search_by_type(self, memory_type: MemoryType, limit: int) -> list[MemoryItem]: ...

    async def list_recent(self, limit: int) -> list[MemoryItem]: ...

    async def increment_access(self, memory_id: str) -> None: ...

    async def search_session(self, session_id: str) -> list[MemoryItem]: ...

    async def store_raw(self, item: MemoryItem) -> str: ...

    async def search_raw(self, query: str, limit: int) -> list[MemoryItem]: ...


@runtime_checkable
class MemoryRetriever(Protocol):
    async def retrieve(
        self,
        query: MemoryQuery,
        scope_id: str | None = None,
        session_id: str | None = None,
    ) -> list[MemoryItem]: ...


@runtime_checkable
class MemoryConsolidator(Protocol):
    async def run_once(self) -> dict[str, int]: ...


@runtime_checkable
class MemoryPortability(Protocol):
    async def export_bundle(
        self, since: datetime | None = None, include_raw: bool = True
    ) -> bytes: ...

    async def import_bundle(self, bundle: bytes, mode: str = "merge") -> dict[str, object]: ...


__all__ = [
    "MemoryConsolidator",
    "MemoryPortability",
    "MemoryRetriever",
    "MemoryStore",
]
