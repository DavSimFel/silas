"""Shared stub implementations for tests."""

from __future__ import annotations

import os
import uuid
import warnings
from dataclasses import dataclass, field


@dataclass(slots=True)
class InMemoryAuditLog:
    """In-memory audit log stub. No hash chaining, no persistence."""

    events: list[dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if os.environ.get("SILAS_TESTING") != "1":
            warnings.warn(
                "InMemoryAuditLog is a stub for testing only. "
                "Use SQLiteAuditLog in production. "
                "Set SILAS_TESTING=1 to suppress this warning.",
                DeprecationWarning,
                stacklevel=2,
            )

    async def log(self, event: str, **data: object) -> str:
        event_id = uuid.uuid4().hex
        self.events.append({"id": event_id, "event": event, "data": data})
        return event_id

    async def verify_chain(self) -> tuple[bool, int]:
        return True, len(self.events)

    async def write_checkpoint(self) -> str:
        return uuid.uuid4().hex

    async def verify_from_checkpoint(self, checkpoint_id: str | None = None) -> tuple[bool, int]:
        del checkpoint_id
        return True, len(self.events)


__all__ = ["InMemoryAuditLog"]
