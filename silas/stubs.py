"""Shared stub implementations for Phase 1a (pre-SQLite).

These are used by both the runtime (main.py) and tests (fakes.py).
They will be replaced by real implementations in Phase 1b.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(slots=True)
class InMemoryAuditLog:
    """In-memory audit log stub. No hash chaining, no persistence."""

    events: list[dict[str, object]] = field(default_factory=list)

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
