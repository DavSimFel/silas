"""The Stream — Silas's permanent orchestration session."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from silas.agents.structured import run_structured_agent
from silas.core.context_manager import LiveContextManager
from silas.core.token_counter import HeuristicTokenCounter
from silas.core.turn_context import TurnContext
from silas.gates import OutputGateRunner
from silas.models.agents import PlanAction, RouteDecision
from silas.models.context import ContextItem, ContextZone
from silas.models.memory import MemoryItem, MemoryType, ReingestionTier
from silas.models.messages import ChannelMessage, SignedMessage, TaintLevel
from silas.protocols.channels import ChannelAdapterCore

_counter = HeuristicTokenCounter()
_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_:-]+)")


@dataclass(slots=True)
class Stream:
    channel: ChannelAdapterCore
    turn_context: TurnContext
    context_manager: LiveContextManager | None = None
    owner_id: str = "owner"
    default_context_profile: str = "conversation"
    output_gate_runner: OutputGateRunner | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        if self.context_manager is not None:
            self.turn_context.context_manager = self.context_manager
            self.turn_context.live_context_manager = self.context_manager
        elif self.turn_context.live_context_manager is not None:
            self.context_manager = self.turn_context.live_context_manager

    async def start(self) -> None:
        self._ensure_session_id()
        await self._rehydrate()
        async for message, connection_id in self.channel.listen():
            await self._process_turn(message, connection_id)

    async def _rehydrate(self) -> None:
        """Restore state from previous run (spec §5.1.3)."""
        tc = self.turn_context
        context_manager = self._context_manager()
        session_id = self._ensure_session_id()

        # Step 1-2: Load recent chronicle entries
        if tc.chronicle_store is not None and context_manager is not None:
            recent = await tc.chronicle_store.get_recent(tc.scope_id, limit=50)
            for item in recent:
                context_manager.add(tc.scope_id, item)
            if recent:
                # Restore turn number from last entry
                tc.turn_number = max(item.turn_number for item in recent)

        # Step 3: Search memory for user profile
        if tc.memory_store is not None and context_manager is not None:
            profile_items = await tc.memory_store.search_keyword("user profile preferences", limit=1)
            for item in profile_items:
                context_manager.add(
                    tc.scope_id,
                    ContextItem(
                        ctx_id=f"memory:profile:{item.memory_id}",
                        zone=ContextZone.memory,
                        content=item.content,
                        token_count=_counter.count(item.content),
                        created_at=datetime.now(timezone.utc),
                        turn_number=tc.turn_number,
                        source="memory:profile",
                        taint=item.taint,
                        kind="memory",
                        pinned=True,
                    ),
                )

        # Step 3b: Rehydrate recent session memories
        if tc.memory_store is not None and context_manager is not None:
            recent_session_memories = await tc.memory_store.search_session(session_id)
            for item in recent_session_memories[:10]:
                context_manager.add(
                    tc.scope_id,
                    ContextItem(
                        ctx_id=f"memory:session:{item.memory_id}",
                        zone=ContextZone.memory,
                        content=item.content,
                        token_count=_counter.count(item.content),
                        created_at=datetime.now(timezone.utc),
                        turn_number=tc.turn_number,
                        source="memory:session_rehydrate",
                        taint=item.taint,
                        kind="memory",
                    ),
                )

        # Step 5: System message
        await self._audit("stream_rehydrated", turn_number=tc.turn_number)

    async def _process_turn(self, message: ChannelMessage, connection_id: str = "owner") -> str:
        session_id = self._ensure_session_id()

        await self._audit("phase1a_noop", step=0, note="active gates precompile skipped")
        await self._audit("phase1a_noop", step=0.5, note="review/proactive queue skipped")
        await self._audit("phase1a_noop", step=1, note="input gates skipped")

        # Step 2: sign/taint classification
        taint = TaintLevel.owner if message.sender_id == self.owner_id else TaintLevel.external
        signed = SignedMessage(
            message=message,
            signature=b"",
            nonce=uuid.uuid4().hex,
            taint=taint,
        )
        context_manager = self._context_manager()

        # Step 3: add inbound message to chronicle zone + persist
        self.turn_context.turn_number += 1
        turn_number = self.turn_context.turn_number
        chronicle_item = ContextItem(
            ctx_id=f"chronicle:{turn_number}:{uuid.uuid4().hex}",
            zone=ContextZone.chronicle,
            content=f"[{signed.taint.value}] {message.sender_id}: {message.text}",
            token_count=_counter.count(message.text),
            created_at=datetime.now(timezone.utc),
            turn_number=turn_number,
            source=f"channel:{message.channel}",
            taint=signed.taint,
            kind="message",
        )
        if context_manager is not None:
            context_manager.add(self.turn_context.scope_id, chronicle_item)
        if self.turn_context.chronicle_store is not None:
            await self.turn_context.chronicle_store.append(self.turn_context.scope_id, chronicle_item)

        # Step 4: auto-retrieve memories
        if self.turn_context.memory_store is not None and context_manager is not None:
            recalled_keyword = await self.turn_context.memory_store.search_keyword(message.text, limit=3)

            recalled_entity: list[MemoryItem] = []
            mentions = self._extract_mentions(message.text)
            if mentions:
                entity_candidates = await self.turn_context.memory_store.search_by_type(
                    MemoryType.entity,
                    limit=50,
                )
                recalled_entity = [
                    item
                    for item in entity_candidates
                    if self._memory_matches_any_mention(item, mentions)
                ]

            recalled_unique: dict[str, MemoryItem] = {}
            for item in [*recalled_keyword, *recalled_entity]:
                recalled_unique.setdefault(item.memory_id, item)

            for item in recalled_unique.values():
                await self.turn_context.memory_store.increment_access(item.memory_id)
                context_manager.add(
                    self.turn_context.scope_id,
                    ContextItem(
                        ctx_id=f"memory:{item.memory_id}",
                        zone=ContextZone.memory,
                        content=item.content,
                        token_count=_counter.count(item.content),
                        created_at=datetime.now(timezone.utc),
                        turn_number=turn_number,
                        source="memory:auto_retrieve",
                        taint=item.taint,
                        kind="memory",
                    ),
                )

        # Step 4.5: Raw memory ingest
        if self.turn_context.memory_store is not None:
            await self.turn_context.memory_store.store_raw(
                MemoryItem(
                    memory_id=f"raw:{self.turn_context.scope_id}:{turn_number}:{uuid.uuid4().hex}",
                    content=message.text,
                    memory_type=MemoryType.episode,
                    reingestion_tier=ReingestionTier.low_reingestion,
                    taint=signed.taint,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    session_id=session_id,
                    source_kind="conversation_raw",
                ),
            )

        await self._audit("phase1a_noop", step=5, note="budget enforcement deferred to post-response")
        available_skills = self._available_skill_names()
        await self._audit(
            "skill_availability_checked",
            step=6,
            available_skills=available_skills,
            has_skills=bool(available_skills),
        )
        await self._audit("phase1a_noop", step=6.5, note="skill-aware toolset preparation deferred")

        # Step 7: render context and route through Proxy
        if self.turn_context.proxy is None:
            raise RuntimeError("turn_context.proxy is required")

        rendered_context = ""
        if context_manager is not None:
            rendered_context = context_manager.render(self.turn_context.scope_id, turn_number)

        routed = await run_structured_agent(
            agent=self.turn_context.proxy,
            prompt=self._build_proxy_prompt(message.text, rendered_context),
            call_name="proxy",
            default_context_profile=self.default_context_profile,
        )
        if not isinstance(routed, RouteDecision):
            raise TypeError("proxy must return RouteDecision")

        if context_manager is not None:
            context_manager.set_profile(self.turn_context.scope_id, routed.context_profile)

        response_text = self._route_response_text(routed)
        if routed.route == "planner":
            plan_actions = self._extract_plan_actions(routed)
            if plan_actions:
                for action in plan_actions:
                    skill_name = self._extract_skill_name(action)
                    await self._audit(
                        "planner_skill_action_stub",
                        turn_number=turn_number,
                        action=action,
                        skill_name=skill_name,
                        skill_registered=(
                            bool(skill_name)
                            and skill_name in available_skills
                        ),
                    )
            else:
                await self._audit("planner_stub_used", turn_number=turn_number, reason=routed.reason)

        gate_results_payload: list[dict[str, object]] = []
        warning_payload: list[dict[str, object]] = []
        blocked_gate_names: list[str] = []
        if self.output_gate_runner is not None:
            response_text, gate_results = self.output_gate_runner.evaluate(
                response_text=response_text,
                response_taint=signed.taint,
                sender_id=message.sender_id,
            )
            gate_results_payload = [
                result.model_dump(mode="json")
                for result in gate_results
            ]
            warning_payload = [
                result.model_dump(mode="json")
                for result in gate_results
                if "warn" in result.flags
            ]
            blocked_gate_names = [
                result.gate_name
                for result in gate_results
                if result.action == "block"
            ]

        await self._audit(
            "output_gates_evaluated",
            turn_number=turn_number,
            results=gate_results_payload,
            configured=self.output_gate_runner is not None,
        )
        if warning_payload:
            await self._audit(
                "output_gate_warnings",
                turn_number=turn_number,
                warnings=warning_payload,
            )
        if blocked_gate_names:
            response_text = "I cannot share that"
            await self._audit(
                "output_gate_blocked",
                turn_number=turn_number,
                blocked_gates=blocked_gate_names,
            )

        await self._audit("phase1a_noop", step=9, note="memory query processing skipped")
        await self._audit("phase1a_noop", step=10, note="memory op processing skipped")

        # Step 11: persist response to chronicle
        response_item = ContextItem(
            ctx_id=f"chronicle:{turn_number}:resp:{uuid.uuid4().hex}",
            zone=ContextZone.chronicle,
            content=f"Silas: {response_text}",
            token_count=_counter.count(response_text),
            created_at=datetime.now(timezone.utc),
            turn_number=turn_number,
            source="agent:proxy",
            taint=TaintLevel.owner,
            kind="message",
        )
        if context_manager is not None:
            context_manager.add(self.turn_context.scope_id, response_item)
        if self.turn_context.chronicle_store is not None:
            await self.turn_context.chronicle_store.append(self.turn_context.scope_id, response_item)

        evicted_ctx_ids: list[str] = []
        if context_manager is not None:
            evicted_ctx_ids = context_manager.enforce_budget(
                self.turn_context.scope_id,
                turn_number=turn_number,
                current_goal=None,
            )
        if evicted_ctx_ids:
            await self._audit(
                "context_budget_enforced",
                turn_number=turn_number,
                evicted_ctx_ids=evicted_ctx_ids,
            )

        await self._audit("phase1a_noop", step=11.5, note="raw output ingest skipped")
        await self._audit("phase1a_noop", step=12, note="plan/approval flow skipped")

        # Step 13: send response
        await self.channel.send(connection_id, response_text, reply_to=message.reply_to)

        await self._audit("phase1a_noop", step=14, note="access state updates skipped")
        await self._audit("phase1a_noop", step=15, note="personality/autonomy post-turn updates skipped")
        await self._audit("turn_processed", turn_number=turn_number, route=routed.route)

        return response_text

    async def _audit(self, event: str, **data: object) -> None:
        if self.turn_context.audit is None:
            return
        await self.turn_context.audit.log(event, **data)

    def _context_manager(self):
        if self.context_manager is not None:
            return self.context_manager
        return self.turn_context.context_manager

    def _build_proxy_prompt(self, message_text: str, rendered_context: str) -> str:
        if not rendered_context.strip():
            return message_text
        return (
            "[CONTEXT]\n"
            f"{rendered_context}\n\n"
            "[USER MESSAGE]\n"
            f"{message_text}"
        )

    def _route_response_text(self, routed: RouteDecision) -> str:
        if routed.route == "planner":
            return self._planner_stub_response()
        return routed.response.message if routed.response is not None else ""

    def _planner_stub_response(self) -> str:
        return (
            "I need to plan this request before execution. "
            "Planner execution is not available yet."
        )

    def _available_skill_names(self) -> list[str]:
        registry = self.turn_context.skill_registry
        if registry is None:
            return []
        return [skill.name for skill in registry.list_all()]

    def _extract_plan_actions(self, routed: RouteDecision) -> list[dict[str, object]]:
        raw_actions = getattr(routed, "plan_actions", None)
        if raw_actions is None:
            return []
        if not isinstance(raw_actions, list):
            return []

        normalized: list[dict[str, object]] = []
        for action in raw_actions:
            if isinstance(action, PlanAction):
                normalized.append(action.model_dump(mode="json"))
            elif isinstance(action, dict):
                normalized.append(action)
        return normalized

    def _extract_skill_name(self, action: dict[str, object]) -> str | None:
        candidate = (
            action.get("skill_name")
            or action.get("skill")
            or action.get("tool")
        )
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        return None

    def _ensure_session_id(self) -> str:
        if self.session_id is None:
            self.session_id = str(uuid.uuid4())
        return self.session_id

    def _extract_mentions(self, message_text: str) -> set[str]:
        return {match.lstrip("@").lower() for match in _MENTION_PATTERN.findall(message_text)}

    def _memory_matches_any_mention(self, item: MemoryItem, mentions: set[str]) -> bool:
        if not mentions:
            return False

        content_lower = item.content.lower()
        memory_id_lower = item.memory_id.lower()
        entity_refs_lower = {ref.lstrip("@").lower() for ref in item.entity_refs}
        semantic_tags_lower = [tag.lstrip("@").lower() for tag in item.semantic_tags]

        for mention in mentions:
            if (
                mention in content_lower
                or mention in memory_id_lower
                or mention in entity_refs_lower
                or any(mention in tag for tag in semantic_tags_lower)
            ):
                return True
        return False


__all__ = ["Stream"]
