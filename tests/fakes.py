from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator

from silas.models.agents import AgentResponse, InteractionMode, InteractionRegister, RouteDecision
from silas.models.context import ContextItem, ContextProfile, ContextSubscription, ContextZone
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import ChannelMessage, TaintLevel
from silas.stubs import InMemoryAuditLog as InMemoryAuditLog  # noqa: PLC0414


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class RunResult:
    output: RouteDecision


class TestModel:
    """Deterministic structured agent used by tests."""

    def __init__(self, message_prefix: str = "echo:") -> None:
        self.message_prefix = message_prefix

    async def run(self, prompt: str) -> RunResult:
        decision = RouteDecision(
            route="direct",
            reason="test_model",
            response=AgentResponse(
                message=f"{self.message_prefix} {prompt}",
                needs_approval=False,
            ),
            interaction_register=InteractionRegister.status,
            interaction_mode=InteractionMode.default_and_offer,
            context_profile="conversation",
        )
        return RunResult(output=decision)


class FakeTokenCounter:
    def count(self, text: str) -> int:
        return len(text.split())


@dataclass(slots=True)
class InMemoryContextManager:
    by_scope: dict[str, list[ContextItem]] = field(default_factory=dict)
    profile_by_scope: dict[str, str] = field(default_factory=dict)

    def add(self, scope_id: str, item: ContextItem) -> str:
        self.by_scope.setdefault(scope_id, []).append(item)
        return item.ctx_id

    def drop(self, scope_id: str, ctx_id: str) -> None:
        items = self.by_scope.get(scope_id, [])
        self.by_scope[scope_id] = [item for item in items if item.ctx_id != ctx_id]

    def get_zone(self, scope_id: str, zone: ContextZone) -> list[ContextItem]:
        return [item for item in self.by_scope.get(scope_id, []) if item.zone == zone]

    def subscribe(self, scope_id: str, sub: ContextSubscription) -> str:
        del scope_id
        return sub.sub_id

    def unsubscribe(self, scope_id: str, sub_id: str) -> None:
        del scope_id, sub_id

    def set_profile(self, scope_id: str, profile_name: str) -> None:
        self.profile_by_scope[scope_id] = profile_name

    def render(self, scope_id: str, turn_number: int) -> str:
        del turn_number
        return "\n".join(item.content for item in self.by_scope.get(scope_id, []))

    def enforce_budget(self, scope_id: str, turn_number: int, current_goal: str | None) -> list[str]:
        del scope_id, turn_number, current_goal
        return []

    def token_usage(self, scope_id: str) -> dict[str, int]:
        items = self.by_scope.get(scope_id, [])
        usage = {
            ContextZone.system.value: 0,
            ContextZone.chronicle.value: 0,
            ContextZone.memory.value: 0,
            ContextZone.workspace.value: 0,
        }
        for item in items:
            usage[item.zone.value] += item.token_count
        return usage


@dataclass(slots=True)
class InMemoryMemoryStore:
    items: dict[str, MemoryItem] = field(default_factory=dict)

    async def store(self, item: MemoryItem) -> str:
        self.items[item.memory_id] = item
        return item.memory_id

    async def get(self, memory_id: str) -> MemoryItem | None:
        return self.items.get(memory_id)

    async def update(self, memory_id: str, **kwargs: object) -> None:
        item = self.items.get(memory_id)
        if item is None:
            return
        payload = item.model_dump(mode="python")
        payload.update(kwargs)
        self.items[memory_id] = MemoryItem.model_validate(payload)

    async def delete(self, memory_id: str) -> None:
        self.items.pop(memory_id, None)

    async def search_keyword(self, query: str, limit: int) -> list[MemoryItem]:
        lower = query.lower()
        results = [item for item in self.items.values() if lower in item.content.lower()]
        return results[:limit]

    async def search_session(self, session_id: str) -> list[MemoryItem]:
        return [item for item in self.items.values() if item.session_id == session_id]

    async def store_raw(self, item: MemoryItem) -> str:
        return await self.store(item)

    async def search_raw(self, query: str, limit: int) -> list[MemoryItem]:
        return await self.search_keyword(query, limit)


@dataclass(slots=True)
class InMemoryChannel:
    channel_name: str = "test"
    outgoing: list[dict[str, object]] = field(default_factory=list)
    incoming: asyncio.Queue[tuple[ChannelMessage, str]] = field(default_factory=asyncio.Queue)

    async def listen(self) -> AsyncIterator[tuple[ChannelMessage, str]]:
        while True:
            yield await self.incoming.get()

    async def send(self, recipient_id: str, text: str, reply_to: str | None = None) -> None:
        self.outgoing.append({"recipient_id": recipient_id, "text": text, "reply_to": reply_to})

    async def push_message(self, text: str, sender_id: str = "owner", scope_id: str = "owner") -> None:
        message = ChannelMessage(
            channel=self.channel_name,
            sender_id=sender_id,
            text=text,
            timestamp=_utc_now(),
        )
        await self.incoming.put((message, scope_id))


def sample_memory_item(memory_id: str, content: str) -> MemoryItem:
    return MemoryItem(
        memory_id=memory_id,
        content=content,
        memory_type=MemoryType.fact,
        taint=TaintLevel.owner,
        source_kind="conversation_raw",
    )


def sample_context_profile(name: str = "conversation") -> ContextProfile:
    return ContextProfile(name=name, chronicle_pct=0.45, memory_pct=0.20, workspace_pct=0.15)


__all__ = [
    "FakeTokenCounter",
    "InMemoryAuditLog",
    "InMemoryChannel",
    "InMemoryContextManager",
    "InMemoryMemoryStore",
    "RunResult",
    "TestModel",
    "sample_context_profile",
    "sample_memory_item",
]
