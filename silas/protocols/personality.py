from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from silas.models.messages import ChannelMessage
from silas.models.personality import AxisProfile, PersonaEvent, PersonaState


@runtime_checkable
class PersonalityEngine(Protocol):
    async def detect_context(self, message: ChannelMessage, route_hint: str | None = None) -> str: ...

    async def get_effective_axes(self, scope_id: str, context_key: str) -> AxisProfile: ...

    async def render_directives(self, scope_id: str, context_key: str) -> str: ...

    async def apply_event(
        self,
        scope_id: str,
        event_type: str,
        trusted: bool,
        source: str,
        metadata: dict[str, object] | None = None,
    ) -> PersonaState: ...

    async def decay(self, scope_id: str, now: datetime) -> PersonaState: ...

    async def set_preset(self, scope_id: str, preset_name: str) -> PersonaState: ...

    async def adjust_axes(
        self,
        scope_id: str,
        delta: dict[str, float],
        trusted: bool,
        persist_to_baseline: bool = False,
    ) -> PersonaState: ...


@runtime_checkable
class PersonaStore(Protocol):
    async def get_state(self, scope_id: str) -> PersonaState | None: ...

    async def save_state(self, state: PersonaState) -> None: ...

    async def append_event(self, event: PersonaEvent) -> None: ...

    async def list_events(self, scope_id: str, limit: int = 100) -> list[PersonaEvent]: ...


__all__ = ["PersonaStore", "PersonalityEngine"]
