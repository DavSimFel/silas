from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from silas.models.messages import ChannelMessage


@runtime_checkable
class PersonalityEngine(Protocol):
    async def detect_context(self, message: ChannelMessage, route_hint: str | None = None) -> str: ...

    async def get_effective_axes(self, scope_id: str, context_key: str) -> object: ...

    async def render_directives(self, scope_id: str, context_key: str) -> str: ...

    async def apply_event(
        self,
        scope_id: str,
        event_type: str,
        trusted: bool,
        source: str,
        metadata: dict[str, object] | None = None,
    ) -> object: ...

    async def decay(self, scope_id: str, now: datetime) -> object: ...

    async def set_preset(self, scope_id: str, preset_name: str) -> object: ...

    async def adjust_axes(
        self,
        scope_id: str,
        delta: dict[str, float],
        trusted: bool,
        persist_to_baseline: bool = False,
    ) -> object: ...


@runtime_checkable
class PersonaStore(Protocol):
    async def get_state(self, scope_id: str) -> object | None: ...

    async def save_state(self, state: object) -> None: ...

    async def append_event(self, event: object) -> None: ...

    async def list_events(self, scope_id: str, limit: int = 100) -> list[object]: ...


__all__ = ["PersonalityEngine", "PersonaStore"]
