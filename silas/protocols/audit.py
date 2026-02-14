from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AuditLog(Protocol):
    async def log(self, event: str, **data: object) -> str: ...

    async def verify_chain(self) -> tuple[bool, int]: ...

    async def write_checkpoint(self) -> str: ...

    async def verify_from_checkpoint(
        self, checkpoint_id: str | None = None
    ) -> tuple[bool, int]: ...


__all__ = ["AuditLog"]
