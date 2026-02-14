from __future__ import annotations

from typing import Protocol, runtime_checkable

from silas.models.context import ContextItem, ContextSubscription, ContextZone


@runtime_checkable
class ContextManager(Protocol):
    def add(self, scope_id: str, item: ContextItem) -> str: ...

    def drop(self, scope_id: str, ctx_id: str) -> None: ...

    def get_zone(self, scope_id: str, zone: ContextZone) -> list[ContextItem]: ...

    def subscribe(self, scope_id: str, sub: ContextSubscription) -> str: ...

    def unsubscribe(self, scope_id: str, sub_id: str) -> None: ...

    def set_profile(self, scope_id: str, profile_name: str) -> None: ...

    def render(self, scope_id: str, turn_number: int) -> str: ...

    def enforce_budget(
        self, scope_id: str, turn_number: int, current_goal: str | None
    ) -> list[str]: ...

    def token_usage(self, scope_id: str) -> dict[str, int]: ...


__all__ = ["ContextManager"]
