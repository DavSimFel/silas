from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EphemeralExecutor(Protocol):
    async def execute(self, envelope: object) -> object: ...


@runtime_checkable
class SandboxManager(Protocol):
    async def create(self, config: object) -> object: ...

    async def destroy(self, sandbox_id: str) -> None: ...


__all__ = ["EphemeralExecutor", "SandboxManager"]
