"""The Stream — Silas's permanent orchestration session."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from silas.agents.structured import run_structured_agent
from silas.core.context_manager import LiveContextManager
from silas.core.plan_parser import MarkdownPlanParser
from silas.core.token_counter import HeuristicTokenCounter
from silas.core.turn_context import TurnContext
from silas.gates import OutputGateRunner
from silas.models.agents import PlanAction, RouteDecision
from silas.models.approval import ApprovalDecision, ApprovalScope, ApprovalToken, ApprovalVerdict
from silas.models.context import ContextItem, ContextZone
from silas.models.memory import MemoryItem, MemoryType, ReingestionTier
from silas.models.messages import ChannelMessage, SignedMessage, TaintLevel
from silas.models.proactivity import SuggestionProposal
from silas.models.work import WorkItem, WorkItemResult, WorkItemStatus, WorkItemType
from silas.protocols.channels import ChannelAdapterCore
from silas.protocols.proactivity import AutonomyCalibrator, SuggestionEngine

_counter = HeuristicTokenCounter()
_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_:-]+)")
_APPROVAL_WAIT_LIMIT = timedelta(minutes=5)


@dataclass(slots=True)
class Stream:
    channel: ChannelAdapterCore
    turn_context: TurnContext
    context_manager: LiveContextManager | None = None
    suggestion_engine: SuggestionEngine | None = None
    autonomy_calibrator: AutonomyCalibrator | None = None
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
        if self.suggestion_engine is not None:
            self.turn_context.suggestion_engine = self.suggestion_engine
        elif self.turn_context.suggestion_engine is not None:
            self.suggestion_engine = self.turn_context.suggestion_engine
        if self.autonomy_calibrator is not None:
            self.turn_context.autonomy_calibrator = self.autonomy_calibrator
        elif self.turn_context.autonomy_calibrator is not None:
            self.autonomy_calibrator = self.turn_context.autonomy_calibrator
        self._register_approval_channel_handler()

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
        high_confidence_suggestions: list[SuggestionProposal] = []
        suggestion_engine = self._suggestion_engine()
        if suggestion_engine is None:
            await self._audit("phase1a_noop", step=0.5, note="review/proactive queue skipped")
        else:
            idle_suggestions = await suggestion_engine.generate_idle(
                self.turn_context.scope_id,
                datetime.now(timezone.utc),
            )
            high_confidence_suggestions = [
                suggestion
                for suggestion in idle_suggestions
                if suggestion.confidence > 0.80
            ]
            low_confidence_suggestions = [
                suggestion
                for suggestion in idle_suggestions
                if suggestion.confidence <= 0.80
            ]
            for suggestion in low_confidence_suggestions:
                await self._push_suggestion_to_side_panel(connection_id, suggestion)
            await self._audit(
                "proactive_queue_reviewed",
                step=0.5,
                surfaced=len(idle_suggestions),
                high_confidence=len(high_confidence_suggestions),
                side_panel=len(low_confidence_suggestions),
            )
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
        plan_flow_payload = {
            "actions_seen": 0,
            "skills_executed": 0,
            "skills_skipped": 0,
            "approval_requested": 0,
            "approval_approved": 0,
            "approval_declined": 0,
        }
        if routed.route == "planner":
            plan_actions = self._extract_plan_actions(routed)
            plan_flow_payload["actions_seen"] = len(plan_actions)
            if plan_actions:
                work_exec_summary = await self._execute_plan_actions(
                    plan_actions,
                    turn_number=turn_number,
                    continuation_of=routed.continuation_of,
                )
                if work_exec_summary is not None:
                    response_text = work_exec_summary
                else:
                    response_text, plan_flow_payload = await self._execute_planner_skill_actions(
                        plan_actions=plan_actions,
                        connection_id=connection_id,
                        turn_number=turn_number,
                        fallback_response=response_text,
                    )
            else:
                await self._audit("planner_stub_used", turn_number=turn_number, reason=routed.reason)
        await self._audit(
            "plan_approval_flow_checked",
            step=12,
            turn_number=turn_number,
            **plan_flow_payload,
        )
        response_text = self._prepend_high_confidence_suggestions(
            response_text,
            high_confidence_suggestions,
        )

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

        # Step 13: send response
        await self.channel.send(connection_id, response_text, reply_to=message.reply_to)

        await self._audit("phase1a_noop", step=14, note="access state updates skipped")
        await self._record_autonomy_outcome(
            turn_number=turn_number,
            route=routed.route,
            blocked=bool(blocked_gate_names),
        )
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

    def _suggestion_engine(self) -> SuggestionEngine | None:
        if self.suggestion_engine is not None:
            return self.suggestion_engine
        return self.turn_context.suggestion_engine

    def _autonomy_calibrator(self) -> AutonomyCalibrator | None:
        if self.autonomy_calibrator is not None:
            return self.autonomy_calibrator
        return self.turn_context.autonomy_calibrator

    def _prepend_high_confidence_suggestions(
        self,
        response_text: str,
        suggestions: list[SuggestionProposal],
    ) -> str:
        if not suggestions:
            return response_text
        preface = "\n".join(f"Suggestion: {suggestion.text}" for suggestion in suggestions)
        if not response_text:
            return preface
        return f"{preface}\n\n{response_text}"

    async def _push_suggestion_to_side_panel(
        self,
        connection_id: str,
        suggestion: SuggestionProposal,
    ) -> None:
        send_suggestion = getattr(self.channel, "send_suggestion", None)
        if callable(send_suggestion):
            await send_suggestion(connection_id, suggestion)
            await self._audit(
                "suggestion_side_panel_enqueued",
                suggestion_id=suggestion.id,
                confidence=suggestion.confidence,
                source=suggestion.source,
                category=suggestion.category,
            )
            return
        await self._audit(
            "suggestion_side_panel_unavailable",
            suggestion_id=suggestion.id,
            confidence=suggestion.confidence,
            source=suggestion.source,
            category=suggestion.category,
        )

    async def _record_autonomy_outcome(
        self,
        *,
        turn_number: int,
        route: str,
        blocked: bool,
    ) -> None:
        calibrator = self._autonomy_calibrator()
        if calibrator is None:
            await self._audit("phase1a_noop", step=15, note="personality/autonomy post-turn updates skipped")
            return
        outcome = "declined" if blocked else "approved"
        await calibrator.record_outcome(
            self.turn_context.scope_id,
            action_family=route,
            outcome=outcome,
        )
        await self._audit(
            "autonomy_calibration_recorded",
            step=15,
            turn_number=turn_number,
            action_family=route,
            outcome=outcome,
        )

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

    async def _execute_plan_actions(
        self,
        plan_actions: list[dict[str, object]],
        *,
        turn_number: int,
        continuation_of: str | None,
    ) -> str | None:
        executor = self.turn_context.work_executor
        if executor is None:
            return None

        try:
            work_items = self._plan_actions_to_work_items(
                plan_actions, turn_number=turn_number, continuation_of=continuation_of,
            )
            ordered_work_items = self._order_work_items(work_items)
        except ValueError as exc:
            await self._audit("planner_actions_invalid", turn_number=turn_number, error=str(exc))
            return f"Planner execution failed: {exc}"

        if not ordered_work_items:
            return "Planner produced no executable work items."

        results: list[WorkItemResult] = []
        for work_item in ordered_work_items:
            result = await executor.execute(work_item)
            results.append(result)
            if result.status != WorkItemStatus.done:
                break

        done_count = sum(1 for r in results if r.status == WorkItemStatus.done)
        failed = [r for r in results if r.status == WorkItemStatus.failed]
        await self._audit(
            "planner_actions_executed", turn_number=turn_number,
            work_item_ids=[item.id for item in ordered_work_items],
            done_count=done_count, failed_count=len(failed),
        )
        if failed:
            f = failed[0]
            return f"Plan execution summary: {done_count} done, {len(failed)} failed. First failure: {f.work_item_id} ({f.last_error or f.summary})."
        return f"Plan execution summary: {done_count} done, 0 failed."

    def _plan_actions_to_work_items(
        self,
        plan_actions: list[dict[str, object]],
        *,
        turn_number: int,
        continuation_of: str | None,
    ) -> list[WorkItem]:
        parser = MarkdownPlanParser()
        work_items: list[WorkItem] = []
        for index, action in enumerate(plan_actions):
            work_item = self._plan_action_to_work_item(action, parser=parser, index=index, turn_number=turn_number)
            if continuation_of and work_item.follow_up_of is None:
                update_data: dict[str, object] = {"follow_up_of": continuation_of}
                if not work_item.input_artifacts_from:
                    update_data["input_artifacts_from"] = ["*"]
                work_item = work_item.model_copy(update=update_data)
            work_items.append(work_item)
        return work_items

    def _plan_action_to_work_item(
        self,
        action: Mapping[str, object],
        *,
        parser: MarkdownPlanParser,
        index: int,
        turn_number: int,
    ) -> WorkItem:
        plan_markdown = action.get("plan_markdown")
        if isinstance(plan_markdown, str) and plan_markdown.strip():
            return parser.parse(plan_markdown)
        explicit_work_item = action.get("work_item")
        if isinstance(explicit_work_item, Mapping):
            return WorkItem.model_validate(dict(explicit_work_item))
        payload = dict(action)
        payload.setdefault("id", f"plan:{turn_number}:{index + 1}")
        payload.setdefault("type", WorkItemType.task.value)
        payload.setdefault("title", f"Plan action {index + 1}")
        body = payload.get("body")
        if not isinstance(body, str) or not body.strip():
            body = payload.get("instruction")
        if not isinstance(body, str) or not body.strip():
            body = payload.get("description")
        if not isinstance(body, str) or not body.strip():
            body = f"Execute planner action {index + 1}."
        payload["body"] = body
        return WorkItem.model_validate(payload)

    def _order_work_items(self, work_items: list[WorkItem]) -> list[WorkItem]:
        if not work_items:
            return []
        by_id: dict[str, WorkItem] = {}
        for item in work_items:
            if item.id in by_id:
                raise ValueError(f"duplicate work item id in plan actions: {item.id}")
            by_id[item.id] = item
        prerequisites: dict[str, set[str]] = {
            item.id: {dep_id for dep_id in item.depends_on if dep_id in by_id}
            for item in work_items
        }
        dependents: dict[str, set[str]] = {item_id: set() for item_id in by_id}
        for item_id, deps in prerequisites.items():
            for dep_id in deps:
                dependents[dep_id].add(item_id)
        ready = sorted(item_id for item_id, deps in prerequisites.items() if not deps)
        ordered_ids: list[str] = []
        while ready:
            current = ready.pop(0)
            ordered_ids.append(current)
            for dependent in sorted(dependents[current]):
                prerequisites[dependent].discard(current)
                if not prerequisites[dependent] and dependent not in ordered_ids and dependent not in ready:
                    ready.append(dependent)
            ready.sort()
        if len(ordered_ids) != len(by_id):
            unresolved = sorted(set(by_id) - set(ordered_ids))
            raise ValueError(f"circular planner dependency detected: {' -> '.join(unresolved)}")
        return [by_id[item_id] for item_id in ordered_ids]

    def _ensure_session_id(self) -> str:
        if self.session_id is None:
            self.session_id = str(uuid.uuid4())
        return self.session_id

    def _approval_manager(self):
        return self.turn_context.approval_manager

    def _register_approval_channel_handler(self) -> None:
        register_handler = getattr(self.channel, "register_approval_response_handler", None)
        if not callable(register_handler):
            return
        if self._approval_manager() is None:
            return
        register_handler(self._on_approval_response)

    async def _on_approval_response(
        self,
        token_id: str,
        verdict: ApprovalVerdict,
        resolved_by: str,
    ) -> None:
        approval_manager = self._approval_manager()
        if approval_manager is None:
            return

        try:
            approval_manager.resolve(token_id, verdict, resolved_by)
        except (KeyError, ValueError):
            await self._audit(
                "approval_response_ignored",
                token_id=token_id,
                verdict=verdict.value,
                resolved_by=resolved_by,
            )
            return

        await self._audit(
            "approval_resolved",
            token_id=token_id,
            verdict=verdict.value,
            resolved_by=resolved_by,
        )

    async def _execute_planner_skill_actions(
        self,
        plan_actions: list[dict[str, object]],
        connection_id: str,
        turn_number: int,
        fallback_response: str,
    ) -> tuple[str, dict[str, int]]:
        payload = {
            "actions_seen": len(plan_actions),
            "skills_executed": 0,
            "skills_skipped": 0,
            "approval_requested": 0,
            "approval_approved": 0,
            "approval_declined": 0,
        }

        skill_registry = self.turn_context.skill_registry
        skill_executor = self.turn_context.skill_executor
        if skill_registry is None or skill_executor is None:
            return fallback_response, payload

        summary_lines: list[str] = []
        for action in plan_actions:
            skill_name = self._extract_skill_name(action)
            if not skill_name:
                continue

            skill_def = skill_registry.get(skill_name)
            await self._audit(
                "planner_skill_action_checked",
                turn_number=turn_number,
                action=action,
                skill_name=skill_name,
                skill_registered=skill_def is not None,
            )
            if skill_def is None:
                payload["skills_skipped"] += 1
                summary_lines.append(f"Skipped skill '{skill_name}': skill not registered.")
                continue

            work_item = self._build_skill_work_item(
                skill_name=skill_name,
                action=action,
                turn_number=turn_number,
                requires_approval=skill_def.requires_approval,
            )
            if skill_def.requires_approval:
                payload["approval_requested"] += 1
                decision, token = await self._request_skill_approval(
                    work_item=work_item,
                    scope=ApprovalScope.tool_type,
                    skill_name=skill_name,
                    connection_id=connection_id,
                    turn_number=turn_number,
                )
                if (
                    decision is None
                    or decision.verdict != ApprovalVerdict.approved
                    or token is None
                ):
                    payload["approval_declined"] += 1
                    payload["skills_skipped"] += 1
                    summary_lines.append(f"Skipped skill '{skill_name}': approval declined.")
                    await self._audit(
                        "skill_execution_skipped_approval",
                        turn_number=turn_number,
                        skill_name=skill_name,
                        verdict=decision.verdict.value if decision is not None else "timed_out",
                    )
                    continue

                payload["approval_approved"] += 1
                work_item.approval_token = token

            inputs = self._extract_skill_inputs(action)
            skill_executor.set_work_item(work_item)
            try:
                result = await skill_executor.execute(skill_name, inputs)
            finally:
                skill_executor.set_work_item(None)

            if result.success:
                payload["skills_executed"] += 1
                summary_lines.append(f"Executed skill '{skill_name}'.")
            else:
                payload["skills_skipped"] += 1
                message = result.error or "execution failed"
                summary_lines.append(f"Failed skill '{skill_name}': {message}.")

            await self._audit(
                "planner_skill_action_executed",
                turn_number=turn_number,
                skill_name=skill_name,
                success=result.success,
                error=result.error,
            )

        if summary_lines:
            return "\n".join(summary_lines), payload
        return fallback_response, payload

    def _build_skill_work_item(
        self,
        skill_name: str,
        action: dict[str, object],
        turn_number: int,
        requires_approval: bool,
    ) -> WorkItem:
        title = action.get("title")
        if not isinstance(title, str) or not title.strip():
            title = f"Execute skill: {skill_name}"

        body = action.get("body")
        if not isinstance(body, str) or not body.strip():
            body = f"Planner requested execution of skill '{skill_name}'."

        return WorkItem(
            id=f"skill:{turn_number}:{uuid.uuid4().hex}",
            type=WorkItemType.task,
            title=title,
            body=body,
            needs_approval=requires_approval,
            skills=[skill_name],
        )

    async def _request_skill_approval(
        self,
        work_item: WorkItem,
        scope: ApprovalScope,
        skill_name: str,
        connection_id: str,
        turn_number: int,
    ) -> tuple[ApprovalDecision | None, ApprovalToken | None]:
        approval_manager = self._approval_manager()
        if approval_manager is None:
            return None, None

        token = approval_manager.request_approval(work_item, scope)
        card = self._approval_card(skill_name, token, work_item, scope)
        sent = await self._send_approval_card(connection_id, card)
        if not sent:
            return None, None

        await self._audit(
            "approval_requested",
            turn_number=turn_number,
            token_id=token.token_id,
            work_item_id=work_item.id,
            scope=scope.value,
            skill_name=skill_name,
        )
        decision = await self._wait_for_approval(token)
        if decision is not None:
            return decision, token

        try:
            decision = approval_manager.resolve(
                token.token_id,
                ApprovalVerdict.declined,
                "system:approval_timeout",
            )
        except (KeyError, ValueError):
            return None, None
        return decision, token

    async def _send_approval_card(self, recipient_id: str, card: dict[str, object]) -> bool:
        send_card = getattr(self.channel, "send_approval_card", None)
        if not callable(send_card):
            await self.channel.send(
                recipient_id,
                "Approval required but this channel cannot render approval cards.",
            )
            return False
        await send_card(recipient_id, card)
        return True

    async def _wait_for_approval(self, token: ApprovalToken) -> ApprovalDecision | None:
        approval_manager = self._approval_manager()
        if approval_manager is None:
            return None

        deadline = min(
            token.expires_at,
            datetime.now(timezone.utc) + _APPROVAL_WAIT_LIMIT,
        )
        while datetime.now(timezone.utc) < deadline:
            decision = approval_manager.check_approval(token.token_id)
            if decision is not None:
                return decision
            await asyncio.sleep(0.1)
        return None

    def _approval_card(
        self,
        skill_name: str,
        token: ApprovalToken,
        work_item: WorkItem,
        scope: ApprovalScope,
    ) -> dict[str, object]:
        return {
            "id": token.token_id,
            "title": f"Approve skill: {skill_name}",
            "risk": self._risk_level(scope),
            "rationale": "This skill requires explicit approval before execution.",
            "details": (
                f"Work item: {work_item.title}\n"
                f"Scope: {scope.value}\n"
                f"Expires: {token.expires_at.isoformat()}"
            ),
            "cta": {
                "approve": "Approve",
                "decline": "Decline",
            },
        }

    def _risk_level(self, scope: ApprovalScope) -> str:
        by_scope = {
            ApprovalScope.full_plan: "high",
            ApprovalScope.single_step: "low",
            ApprovalScope.step_range: "medium",
            ApprovalScope.tool_type: "medium",
            ApprovalScope.skill_install: "high",
            ApprovalScope.credential_use: "high",
            ApprovalScope.budget: "medium",
            ApprovalScope.self_update: "high",
            ApprovalScope.connection_act: "high",
            ApprovalScope.connection_manage: "high",
            ApprovalScope.autonomy_threshold: "high",
            ApprovalScope.standing: "high",
        }
        return by_scope.get(scope, "medium")

    def _extract_skill_inputs(self, action: dict[str, object]) -> dict[str, object]:
        for key in ("inputs", "args", "arguments"):
            value = action.get(key)
            if isinstance(value, dict):
                return dict(value)
        return {}

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
