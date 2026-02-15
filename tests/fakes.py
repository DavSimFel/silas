from __future__ import annotations

import asyncio
import hmac
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256

from silas.models.agents import AgentResponse, InteractionMode, InteractionRegister, RouteDecision
from silas.models.context import ContextItem, ContextProfile, ContextSubscription, ContextZone
from silas.models.execution import VerificationReport, VerificationResult
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import ChannelMessage, TaintLevel
from silas.models.personality import AxisProfile, MoodState, PersonaState, VoiceConfig
from silas.models.proactivity import SuggestionProposal
from silas.models.work import (
    BudgetUsed,
    VerificationCheck,
    WorkItem,
    WorkItemResult,
    WorkItemStatus,
)

from tests.stubs import InMemoryAuditLog as InMemoryAuditLog


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class RunResult:
    output: RouteDecision


class FakeModel:
    """Deterministic structured agent used by tests."""

    def __init__(self, message_prefix: str = "echo:") -> None:
        self.message_prefix = message_prefix

    async def run(self, prompt: str) -> RunResult:
        user_prompt = prompt
        marker = "\n\n[USER MESSAGE]\n"
        if marker in prompt:
            user_prompt = prompt.split(marker, 1)[1]

        decision = RouteDecision(
            route="direct",
            reason="test_model",
            response=AgentResponse(
                message=f"{self.message_prefix} {user_prompt}",
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

    def enforce_budget(
        self, scope_id: str, turn_number: int, current_goal: str | None
    ) -> list[str]:
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
    incremented_ids: list[str] = field(default_factory=list)

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

    async def search_keyword(
        self, query: str, limit: int, *, session_id: str | None = None
    ) -> list[MemoryItem]:
        lower = query.lower()
        results = [item for item in self.items.values() if lower in item.content.lower()]
        if session_id is not None:
            results = [
                item
                for item in results
                if getattr(item, "session_id", None) is None
                or getattr(item, "session_id", None) == session_id
            ]
        return results[:limit]

    async def search_by_type(
        self, memory_type: MemoryType, limit: int, *, session_id: str | None = None
    ) -> list[MemoryItem]:
        results = [item for item in self.items.values() if item.memory_type == memory_type]
        results.sort(
            key=lambda item: (
                item.updated_at,
                item.created_at,
                item.memory_id,
            ),
            reverse=True,
        )
        return results[:limit]

    async def list_recent(self, limit: int) -> list[MemoryItem]:
        results = list(self.items.values())
        results.sort(
            key=lambda item: (
                item.updated_at,
                item.created_at,
                item.memory_id,
            ),
            reverse=True,
        )
        return results[:limit]

    async def increment_access(self, memory_id: str) -> None:
        item = self.items.get(memory_id)
        if item is None:
            return
        now = datetime.now(UTC)
        payload = item.model_dump(mode="python")
        payload["access_count"] = item.access_count + 1
        payload["last_accessed"] = now
        payload["updated_at"] = now
        self.items[memory_id] = MemoryItem.model_validate(payload)
        self.incremented_ids.append(memory_id)

    async def search_session(self, session_id: str) -> list[MemoryItem]:
        results = [item for item in self.items.values() if item.session_id == session_id]
        results.sort(
            key=lambda item: (
                item.updated_at,
                item.created_at,
                item.memory_id,
            ),
            reverse=True,
        )
        return results

    async def store_raw(self, item: MemoryItem) -> str:
        return await self.store(item)

    async def search_raw(self, query: str, limit: int) -> list[MemoryItem]:
        return await self.search_keyword(query, limit)


@dataclass(slots=True)
class InMemoryWorkItemStore:
    items: dict[str, WorkItem] = field(default_factory=dict)
    status_updates: list[tuple[str, WorkItemStatus, BudgetUsed]] = field(default_factory=list)

    async def save(self, item: WorkItem) -> None:
        self.items[item.id] = item.model_copy(deep=True)

    async def get(self, work_item_id: str) -> WorkItem | None:
        item = self.items.get(work_item_id)
        return item.model_copy(deep=True) if item is not None else None

    async def list_by_status(self, status: WorkItemStatus) -> list[WorkItem]:
        return [i.model_copy(deep=True) for i in self.items.values() if i.status == status]

    async def list_by_parent(self, parent_id: str) -> list[WorkItem]:
        return [i.model_copy(deep=True) for i in self.items.values() if i.parent == parent_id]

    async def update_status(
        self, work_item_id: str, status: WorkItemStatus, budget_used: BudgetUsed
    ) -> None:
        self.status_updates.append((work_item_id, status, budget_used.model_copy(deep=True)))
        item = self.items.get(work_item_id)
        if item is not None:
            self.items[work_item_id] = item.model_copy(
                update={"status": status, "budget_used": budget_used.model_copy(deep=True)}
            )


@dataclass(slots=True)
class InMemoryChannel:
    channel_name: str = "test"
    outgoing: list[dict[str, object]] = field(default_factory=list)
    stream_events: list[dict[str, object]] = field(default_factory=list)
    suggestion_cards: list[dict[str, object]] = field(default_factory=list)
    incoming: asyncio.Queue[tuple[ChannelMessage, str]] = field(default_factory=asyncio.Queue)

    async def listen(self) -> AsyncIterator[tuple[ChannelMessage, str]]:
        while True:
            yield await self.incoming.get()

    async def send(self, recipient_id: str, text: str, reply_to: str | None = None) -> None:
        self.outgoing.append({"recipient_id": recipient_id, "text": text, "reply_to": reply_to})

    async def send_stream_start(self, connection_id: str) -> None:
        self.stream_events.append({"type": "stream_start", "connection_id": connection_id})

    async def send_stream_chunk(self, connection_id: str, text: str) -> None:
        self.stream_events.append(
            {"type": "stream_chunk", "connection_id": connection_id, "text": text}
        )

    async def send_stream_end(self, connection_id: str) -> None:
        self.stream_events.append({"type": "stream_end", "connection_id": connection_id})

    async def send_suggestion(self, recipient_id: str, suggestion: object) -> dict[str, object]:
        self.suggestion_cards.append({"recipient_id": recipient_id, "suggestion": suggestion})
        return {"selected_value": None, "freetext": None, "approved": False}

    async def push_message(
        self, text: str, sender_id: str = "owner", scope_id: str = "owner"
    ) -> None:
        message = ChannelMessage(
            channel=self.channel_name,
            sender_id=sender_id,
            text=text,
            timestamp=_utc_now(),
        )
        await self.incoming.put((message, scope_id))


@dataclass(slots=True)
class FakeSuggestionEngine:
    idle_by_scope: dict[str, list[SuggestionProposal]] = field(default_factory=dict)
    post_execution_by_scope: dict[str, list[SuggestionProposal]] = field(default_factory=dict)
    idle_calls: list[tuple[str, datetime]] = field(default_factory=list)
    post_execution_calls: list[tuple[str, WorkItemResult]] = field(default_factory=list)
    handled_calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def generate_idle(self, scope_id: str, now: datetime) -> list[SuggestionProposal]:
        self.idle_calls.append((scope_id, now))
        return list(self.idle_by_scope.get(scope_id, []))

    async def generate_post_execution(
        self,
        scope_id: str,
        result: WorkItemResult,
    ) -> list[SuggestionProposal]:
        self.post_execution_calls.append((scope_id, result))
        return list(self.post_execution_by_scope.get(scope_id, []))

    async def mark_handled(self, scope_id: str, suggestion_id: str, outcome: str) -> None:
        self.handled_calls.append((scope_id, suggestion_id, outcome))


@dataclass(slots=True)
class FakeAutonomyCalibrator:
    proposals_by_scope: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    evaluate_calls: list[tuple[str, datetime]] = field(default_factory=list)
    record_calls: list[tuple[str, str, str]] = field(default_factory=list)
    apply_calls: list[tuple[dict[str, object], str]] = field(default_factory=list)
    rollback_calls: list[tuple[str, str]] = field(default_factory=list)

    async def record_outcome(self, scope_id: str, action_family: str, outcome: str) -> None:
        self.record_calls.append((scope_id, action_family, outcome))

    async def evaluate(self, scope_id: str, now: datetime) -> list[dict[str, object]]:
        self.evaluate_calls.append((scope_id, now))
        return list(self.proposals_by_scope.get(scope_id, []))

    async def apply(self, proposal: dict[str, object], decision: str) -> dict[str, object]:
        self.apply_calls.append((proposal, decision))
        return {"proposal": proposal, "decision": decision}

    def rollback(self, scope_id: str, action_family: str) -> None:
        self.rollback_calls.append((scope_id, action_family))

    def get_metrics(self, scope_id: str) -> dict[str, object]:
        return {"scope_id": scope_id, "total_events": len(self.record_calls), "families": {}}


def _neutral_axes() -> AxisProfile:
    return AxisProfile(
        warmth=0.5,
        assertiveness=0.5,
        verbosity=0.5,
        formality=0.5,
        humor=0.5,
        initiative=0.5,
        certainty=0.5,
    )


def _default_voice() -> VoiceConfig:
    return VoiceConfig(tone="neutral", quirks=[], speech_patterns=[], anti_patterns=[])


def _default_persona_state(scope_id: str) -> PersonaState:
    return PersonaState(
        scope_id=scope_id,
        baseline_axes=_neutral_axes(),
        mood=MoodState(energy=0.5, patience=0.5, curiosity=0.5, frustration=0.5),
        active_preset="default",
        voice=_default_voice(),
        last_context="",
        updated_at=_utc_now(),
    )


@dataclass(slots=True)
class FakePersonalityEngine:
    default_context: str = "default"
    axes_default: AxisProfile = field(default_factory=_neutral_axes)
    directives_default: str = "Use a balanced, neutral style."
    states_by_scope: dict[str, PersonaState] = field(default_factory=dict)
    context_matches: dict[str, str] = field(default_factory=dict)
    axes_by_scope_context: dict[tuple[str, str], AxisProfile] = field(default_factory=dict)
    directives_by_scope_context: dict[tuple[str, str], str] = field(default_factory=dict)
    detect_calls: list[tuple[ChannelMessage, str | None]] = field(default_factory=list)
    effective_calls: list[tuple[str, str]] = field(default_factory=list)
    render_calls: list[tuple[str, str]] = field(default_factory=list)
    apply_calls: list[tuple[str, str, bool, str, dict[str, object] | None]] = field(
        default_factory=list
    )
    decay_calls: list[tuple[str, datetime]] = field(default_factory=list)
    preset_calls: list[tuple[str, str]] = field(default_factory=list)
    adjust_calls: list[tuple[str, dict[str, float], bool, bool]] = field(default_factory=list)

    async def detect_context(self, message: ChannelMessage, route_hint: str | None = None) -> str:
        self.detect_calls.append((message, route_hint))
        if route_hint:
            return route_hint
        lower_text = message.text.lower()
        for fragment, context in self.context_matches.items():
            if fragment.lower() in lower_text:
                return context
        return self.default_context

    async def get_effective_axes(self, scope_id: str, context_key: str) -> AxisProfile:
        self.effective_calls.append((scope_id, context_key))
        axes = self.axes_by_scope_context.get((scope_id, context_key), self.axes_default)
        return axes.model_copy(deep=True)

    async def render_directives(self, scope_id: str, context_key: str) -> str:
        self.render_calls.append((scope_id, context_key))
        return self.directives_by_scope_context.get(
            (scope_id, context_key), self.directives_default
        )

    async def apply_event(
        self,
        scope_id: str,
        event_type: str,
        trusted: bool,
        source: str,
        metadata: dict[str, object] | None = None,
    ) -> PersonaState:
        self.apply_calls.append((scope_id, event_type, trusted, source, metadata))
        state = self.states_by_scope.get(scope_id, _default_persona_state(scope_id))
        self.states_by_scope[scope_id] = state.model_copy(
            update={"updated_at": _utc_now()},
            deep=True,
        )
        return self.states_by_scope[scope_id].model_copy(deep=True)

    async def decay(self, scope_id: str, now: datetime) -> PersonaState:
        self.decay_calls.append((scope_id, now))
        state = self.states_by_scope.get(scope_id, _default_persona_state(scope_id))
        self.states_by_scope[scope_id] = state.model_copy(update={"updated_at": now}, deep=True)
        return self.states_by_scope[scope_id].model_copy(deep=True)

    async def set_preset(self, scope_id: str, preset_name: str) -> PersonaState:
        self.preset_calls.append((scope_id, preset_name))
        state = self.states_by_scope.get(scope_id, _default_persona_state(scope_id))
        self.states_by_scope[scope_id] = state.model_copy(
            update={"active_preset": preset_name, "updated_at": _utc_now()},
            deep=True,
        )
        return self.states_by_scope[scope_id].model_copy(deep=True)

    async def adjust_axes(
        self,
        scope_id: str,
        delta: dict[str, float],
        trusted: bool,
        persist_to_baseline: bool = False,
    ) -> PersonaState:
        self.adjust_calls.append((scope_id, dict(delta), trusted, persist_to_baseline))
        state = self.states_by_scope.get(scope_id, _default_persona_state(scope_id))
        self.states_by_scope[scope_id] = state.model_copy(
            update={"updated_at": _utc_now()},
            deep=True,
        )
        return self.states_by_scope[scope_id].model_copy(deep=True)


@dataclass(slots=True)
class FakeVerificationRunner:
    report: VerificationReport = field(
        default_factory=lambda: VerificationReport(
            all_passed=True,
            results=[VerificationResult(name="default", passed=True, reason="passed")],
            failed=[],
        )
    )
    run_calls: list[list[VerificationCheck]] = field(default_factory=list)

    async def run_checks(self, checks: list[VerificationCheck]) -> VerificationReport:
        self.run_calls.append([check.model_copy(deep=True) for check in checks])
        return self.report.model_copy(deep=True)


@dataclass(slots=True)
class FakeKeyManager:
    private_by_owner: dict[str, bytes] = field(default_factory=dict)
    private_by_public: dict[str, bytes] = field(default_factory=dict)

    def generate_keypair(self, owner_id: str) -> str:
        private_key = sha256(owner_id.encode("utf-8")).digest()
        public_key = sha256(private_key).hexdigest()
        self.private_by_owner[owner_id] = private_key
        self.private_by_public[public_key] = private_key
        return public_key

    def sign(self, owner_id: str, payload: bytes) -> bytes:
        private_key = self.private_by_owner.get(owner_id)
        if private_key is None:
            raise KeyError(f"no private key for owner '{owner_id}'")
        return sha256(private_key + payload).digest()

    def verify(self, public_key_hex: str, payload: bytes, signature: bytes) -> tuple[bool, str]:
        private_key = self.private_by_public.get(public_key_hex)
        if private_key is None:
            return False, "Unknown key"
        expected = sha256(private_key + payload).digest()
        if hmac.compare_digest(expected, signature):
            return True, "Valid"
        return False, "Invalid signature"


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
    "FakeAutonomyCalibrator",
    "FakeKeyManager",
    "FakeModel",
    "FakePersonalityEngine",
    "FakeSuggestionEngine",
    "FakeTokenCounter",
    "FakeVerificationRunner",
    "InMemoryAuditLog",
    "InMemoryChannel",
    "InMemoryContextManager",
    "InMemoryMemoryStore",
    "InMemoryWorkItemStore",
    "RunResult",
    "sample_context_profile",
    "sample_memory_item",
]
