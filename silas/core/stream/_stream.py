"""The Stream — Silas's permanent orchestration session.

Core turn-processing loop. Plan execution and approval flow are
delegated to silas.core.plan_executor and silas.core.approval_flow
to keep this file focused on orchestration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from silas.agents.structured import run_structured_agent
from silas.core.approval_flow import ApprovalFlow
from silas.core.context_manager import LiveContextManager
from silas.core.interaction_mode import resolve_interaction_mode
from silas.core.logging import correlation_scope
from silas.core.stream._gates import GateMixin
from silas.core.stream._helpers import HelpersMixin
from silas.core.stream._memory import MemoryMixin
from silas.core.stream._nonce import _InMemoryNonceStore
from silas.core.stream._planner import PlannerMixin
from silas.core.stream._rehydration import RehydrationMixin
from silas.core.stream._signing import SigningMixin
from silas.core.stream._toolsets import ToolsetMixin
from silas.core.token_counter import HeuristicTokenCounter
from silas.core.turn_context import TurnContext
from silas.models.agents import RouteDecision
from silas.models.approval import ApprovalVerdict
from silas.models.connections import ConnectionFailure
from silas.models.context import ContextItem, ContextZone
from silas.models.messages import ChannelMessage, TaintLevel
from silas.models.proactivity import SuggestionProposal
from silas.models.work import WorkItem
from silas.protocols.approval import NonceStore
from silas.protocols.channels import ChannelAdapterCore
from silas.protocols.connections import ConnectionManager
from silas.protocols.proactivity import AutonomyCalibrator, SuggestionEngine
from silas.protocols.scheduler import TaskScheduler
from silas.protocols.work import PlanParser, WorkItemStore
from silas.security.taint import TaintTracker

if TYPE_CHECKING:
    from silas.gates import SilasGateRunner
    from silas.queue.bridge import QueueBridge

_counter = HeuristicTokenCounter()

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TurnProcessor:
    """Per-connection turn processor state container.

    Why: Stream needs isolated mutable turn state by connection to prevent
    chronicle/memory/workspace leakage across concurrent customer sessions.
    """

    connection_key: str
    turn_context: TurnContext
    session_id: str


@dataclass
class Stream(
    HelpersMixin,
    SigningMixin,
    MemoryMixin,
    GateMixin,
    PlannerMixin,
    RehydrationMixin,
    ToolsetMixin,
):
    channel: ChannelAdapterCore
    turn_context: TurnContext
    context_manager: LiveContextManager | None = None
    channels: tuple[ChannelAdapterCore, ...] | list[ChannelAdapterCore] | None = None
    scheduler: TaskScheduler | None = None
    plan_parser: PlanParser | None = None
    work_item_store: WorkItemStore | None = None
    goal_manager: object | None = None
    connection_manager: ConnectionManager | None = None
    suggestion_engine: SuggestionEngine | None = None
    autonomy_calibrator: AutonomyCalibrator | None = None
    # Why optional: queue infra is initialized by default when config.execution.use_queue_path
    # is True (the default). Falls back to procedural path if init fails or is disabled.
    queue_bridge: QueueBridge | None = None
    owner_id: str = "owner"
    default_context_profile: str = "conversation"
    output_gate_runner: SilasGateRunner | None = None
    session_id: str | None = None
    _approval_flow: ApprovalFlow | None = None
    _pending_persona_scopes: set[str] = field(default_factory=set, init=False, repr=False)
    _turn_processors: dict[str, TurnProcessor] = field(default_factory=dict, init=False, repr=False)
    _connection_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)
    _active_turn_context: ContextVar[TurnContext | None] = field(
        default_factory=lambda: ContextVar("stream_active_turn_context", default=None),
        init=False,
        repr=False,
    )
    _active_session_id: ContextVar[str | None] = field(
        default_factory=lambda: ContextVar("stream_active_session_id", default=None),
        init=False,
        repr=False,
    )
    _multi_connection_mode: bool = field(default=False, init=False, repr=False)
    # Startup should inject an Ed25519 key; bytes remains for legacy HMAC-only tests.
    _signing_key: Ed25519PrivateKey | bytes | None = None
    _nonce_store: NonceStore | None = None

    def __post_init__(self) -> None:
        if self._signing_key is None:
            self._signing_key = uuid.uuid4().bytes + uuid.uuid4().bytes
        if self._nonce_store is None:
            self._nonce_store = _InMemoryNonceStore()
        self._sync_turn_context_fields()
        self._register_turn_processor(
            self.owner_id,
            scope_id=self.turn_context.scope_id,
            turn_context=self.turn_context,
            session_id=self._ensure_session_id(),
        )
        self._approval_flow = ApprovalFlow(
            approval_manager=self.turn_context.approval_manager,
            channel=self.channel,
        )
        self._approval_flow.register_channel_handler(
            self.channel, self._on_approval_response,
        )

    def _sync_turn_context_fields(self) -> None:
        """Bidirectional sync of optional fields between Stream and TurnContext."""
        tc = self.turn_context
        if self.context_manager is not None:
            tc.context_manager = self.context_manager
            tc.live_context_manager = self.context_manager
        elif tc.live_context_manager is not None:
            self.context_manager = tc.live_context_manager
        if self.suggestion_engine is not None:
            tc.suggestion_engine = self.suggestion_engine
        elif tc.suggestion_engine is not None:
            self.suggestion_engine = tc.suggestion_engine
        if self.autonomy_calibrator is not None:
            tc.autonomy_calibrator = self.autonomy_calibrator
        elif tc.autonomy_calibrator is not None:
            self.autonomy_calibrator = tc.autonomy_calibrator

    def _turn_context(self) -> TurnContext:
        active_turn_context = self._active_turn_context.get()
        if active_turn_context is not None:
            return active_turn_context
        return self.turn_context

    def _derive_connection_key(self, connection_id: str) -> str:
        normalized_connection_id = connection_id.strip() if connection_id.strip() else self.owner_id
        if normalized_connection_id == self.owner_id and not self._multi_connection_mode:
            return self.owner_id
        self._multi_connection_mode = True
        return normalized_connection_id

    def _register_turn_processor(
        self,
        connection_key: str,
        *,
        scope_id: str,
        turn_context: TurnContext,
        session_id: str,
    ) -> TurnProcessor:
        turn_context.scope_id = scope_id
        processor = TurnProcessor(
            connection_key=connection_key,
            turn_context=turn_context,
            session_id=session_id,
        )
        self._turn_processors[connection_key] = processor
        self._pending_persona_scopes.add(scope_id)
        self._connection_locks.setdefault(connection_key, asyncio.Lock())
        return processor

    def _build_scoped_turn_context(self, scope_id: str) -> TurnContext:
        base = self.turn_context
        scoped = TurnContext(
            scope_id=scope_id,
            context_manager=base.context_manager,
            live_context_manager=base.live_context_manager,
            memory_store=base.memory_store,
            chronicle_store=base.chronicle_store,
            proxy=base.proxy,
            planner=base.planner,
            work_executor=base.work_executor,
            gate_runner=base.gate_runner,
            embedder=base.embedder,
            personality_engine=base.personality_engine,
            skill_loader=base.skill_loader,
            skill_resolver=base.skill_resolver,
            skill_registry=base.skill_registry,
            skill_executor=base.skill_executor,
            approval_manager=base.approval_manager,
            suggestion_engine=base.suggestion_engine,
            autonomy_calibrator=base.autonomy_calibrator,
            audit=base.audit,
            config=base.config,
            turn_number=0,
        )
        return scoped

    def _get_or_create_turn_processor(self, connection_id: str) -> TurnProcessor:
        connection_key = self._derive_connection_key(connection_id)
        processor = self._turn_processors.get(connection_key)
        if processor is not None:
            return processor

        if connection_key == self.owner_id and connection_key not in self._turn_processors:
            return self._register_turn_processor(
                connection_key,
                scope_id=self.owner_id,
                turn_context=self.turn_context,
                session_id=self._ensure_session_id(),
            )

        return self._register_turn_processor(
            connection_key,
            scope_id=connection_key,
            turn_context=self._build_scoped_turn_context(connection_key),
            session_id=str(uuid.uuid4()),
        )

    def _activate_turn_processor(
        self, processor: TurnProcessor,
    ) -> tuple[Token[TurnContext | None], Token[str | None]]:
        turn_token = self._active_turn_context.set(processor.turn_context)
        session_token = self._active_session_id.set(processor.session_id)
        return turn_token, session_token

    def _deactivate_turn_processor(
        self,
        turn_token: Token[TurnContext | None],
        session_token: Token[str | None],
    ) -> None:
        self._active_turn_context.reset(turn_token)
        self._active_session_id.reset(session_token)

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize startup orchestration before accepting live messages."""
        self._ensure_session_id()
        await self._start_queue_orchestrator()
        await self._rehydrate()
        await self._audit("stream_started", started_at=datetime.now(UTC).isoformat())
        await self._run_connection_health_checks()
        await self._load_active_goal_schedule()
        await self._register_heartbeat_jobs()

        listeners = [self._listen_channel(channel) for channel in self._iter_channels()]
        await asyncio.gather(*listeners)

    async def stop(self) -> None:
        """Graceful shutdown — stops queue orchestrator if running."""
        await self._stop_queue_orchestrator()
        await self._audit("stream_stopped", stopped_at=datetime.now(UTC).isoformat())

    async def _listen_channel(self, channel: ChannelAdapterCore) -> None:
        async for message, connection_id in channel.listen():
            await self._process_turn(message, connection_id)

    def _iter_channels(self) -> tuple[ChannelAdapterCore, ...]:
        raw_channels = self.channels
        if raw_channels is None:
            return (self.channel,)

        deduped: list[ChannelAdapterCore] = []
        for channel in raw_channels:
            if channel in deduped:
                continue
            deduped.append(channel)
        if self.channel not in deduped:
            deduped.insert(0, self.channel)
        return tuple(deduped)

    async def _run_connection_health_checks(self) -> None:
        manager = self.connection_manager
        if manager is None:
            await self._audit("stream_startup_dependency_missing", dependency="connection_manager")
            return
        if not self._has_active_goal_connection_dependencies():
            await self._audit(
                "connection_health_checks_skipped",
                reason="no_active_goal_connection_dependencies",
            )
            return

        connections = await manager.list_connections()
        active_connections = [
            connection for connection in connections if getattr(connection, "status", "") == "active"
        ]
        active_connections.sort(key=lambda connection: connection.connection_id)
        health_results = await manager.run_health_checks()

        unhealthy = 0
        for connection, health in zip(active_connections, health_results, strict=False):
            if health.healthy:
                continue

            unhealthy += 1
            recovered, reason = await manager.recover(connection.connection_id)
            await self._audit(
                "connection_recovery_attempted",
                connection_id=connection.connection_id,
                recovered=recovered,
                reason=reason,
            )
            if recovered:
                continue
            await self._notify_recovery_failure(connection.connection_id, reason)

        await self._audit(
            "connection_health_checks_completed",
            checked=len(health_results),
            unhealthy=unhealthy,
        )

    async def _notify_recovery_failure(self, connection_id: str, reason: str) -> None:
        send_failure = getattr(self.channel, "send_connection_failure", None)
        if callable(send_failure):
            await send_failure(
                self.owner_id,
                ConnectionFailure(
                    failure_type="health_check",
                    service=connection_id,
                    message=reason,
                ),
            )
            return
        await self.channel.send(
            self.owner_id,
            f"Connection '{connection_id}' is unhealthy and auto-recovery failed: {reason}",
        )

    async def _load_active_goal_schedule(self) -> None:
        raw_active_goal = self._config_value("active_goal")
        if not isinstance(raw_active_goal, str) or not raw_active_goal.strip():
            return

        if self.plan_parser is None:
            await self._audit("stream_startup_dependency_missing", dependency="plan_parser")
            return

        try:
            markdown = Path(raw_active_goal).read_text(encoding="utf-8")
        except OSError as exc:
            await self._audit("active_goal_load_failed", path=raw_active_goal, error=str(exc))
            return

        try:
            active_goal = self.plan_parser.parse(markdown)
        except ValueError as exc:
            await self._audit("active_goal_parse_failed", path=raw_active_goal, error=str(exc))
            return

        await self._audit(
            "active_goal_loaded",
            goal_id=active_goal.id,
            schedule=active_goal.schedule,
        )

        if not self._is_cron_schedule(active_goal.schedule):
            return

        if self.scheduler is None:
            await self._audit("stream_startup_dependency_missing", dependency="scheduler")
            return

        async def _run_goal() -> None:
            await self._run_scheduled_goal(active_goal)

        try:
            self.scheduler.add_cron_job(f"goal:{active_goal.id}", active_goal.schedule, _run_goal)
        except ValueError as exc:
            await self._audit(
                "active_goal_schedule_registration_failed",
                goal_id=active_goal.id,
                schedule=active_goal.schedule,
                error=str(exc),
            )
            return

        await self._audit(
            "active_goal_schedule_registered",
            goal_id=active_goal.id,
            schedule=active_goal.schedule,
        )

    async def _run_scheduled_goal(self, active_goal: WorkItem) -> None:
        executor = self._turn_context().work_executor
        if executor is None:
            await self._audit("stream_startup_dependency_missing", dependency="work_executor")
            return
        await executor.execute(active_goal.model_copy(deep=True))

    async def _register_heartbeat_jobs(self) -> None:
        if self.scheduler is None:
            await self._audit("stream_startup_dependency_missing", dependency="scheduler")
            return

        await self._register_suggestion_heartbeat()
        await self._register_autonomy_heartbeat()

    async def _register_suggestion_heartbeat(self) -> None:
        enabled = bool(self._config_value("suggestions", "enabled", default=False))
        if not enabled:
            return

        suggestion_engine = self._get_suggestion_engine()
        if suggestion_engine is None:
            await self._audit("stream_startup_dependency_missing", dependency="suggestion_engine")
            return

        cron = self._config_value("suggestions", "heartbeat_cron")
        if not isinstance(cron, str) or not cron.strip():
            await self._audit("suggestion_heartbeat_skipped", reason="missing_cron")
            return

        async def _run_suggestion_heartbeat() -> None:
            await self._suggestion_heartbeat_tick()

        try:
            self.scheduler.add_cron_job(
                "heartbeat:suggestions",
                cron,
                _run_suggestion_heartbeat,
            )
        except ValueError as exc:
            await self._audit(
                "suggestion_heartbeat_registration_failed",
                cron=cron,
                error=str(exc),
            )
            return
        await self._audit("suggestion_heartbeat_registered", cron=cron)

    async def _register_autonomy_heartbeat(self) -> None:
        enabled = bool(self._config_value("autonomy", "enabled", default=False))
        if not enabled:
            return

        autonomy_calibrator = self._get_autonomy_calibrator()
        if autonomy_calibrator is None:
            await self._audit("stream_startup_dependency_missing", dependency="autonomy_calibrator")
            return

        raw_cron = self._config_value("autonomy", "heartbeat_cron")
        if not isinstance(raw_cron, str) or not raw_cron.strip():
            raw_cron = self._config_value("suggestions", "heartbeat_cron")
        if not isinstance(raw_cron, str) or not raw_cron.strip():
            await self._audit("autonomy_heartbeat_skipped", reason="missing_cron")
            return

        async def _run_autonomy_heartbeat() -> None:
            await self._autonomy_heartbeat_tick()

        try:
            self.scheduler.add_cron_job(
                "heartbeat:autonomy",
                raw_cron,
                _run_autonomy_heartbeat,
            )
        except ValueError as exc:
            await self._audit(
                "autonomy_heartbeat_registration_failed",
                cron=raw_cron,
                error=str(exc),
            )
            return
        await self._audit("autonomy_heartbeat_registered", cron=raw_cron)

    async def _suggestion_heartbeat_tick(self) -> None:
        suggestion_engine = self._get_suggestion_engine()
        if suggestion_engine is None:
            return

        now = datetime.now(UTC)
        total_suggestions = 0
        for scope_id in self._known_scopes():
            surfaced = await suggestion_engine.generate_idle(scope_id, now)
            total_suggestions += len(surfaced)

        await self._audit(
            "suggestion_heartbeat_polled",
            scopes=self._known_scopes(),
            surfaced=total_suggestions,
        )

    async def _autonomy_heartbeat_tick(self) -> None:
        autonomy_calibrator = self._get_autonomy_calibrator()
        if autonomy_calibrator is None:
            return

        now = datetime.now(UTC)
        total_proposals = 0
        for scope_id in self._known_scopes():
            proposals = await autonomy_calibrator.evaluate(scope_id, now)
            total_proposals += len(proposals)

        await self._audit(
            "autonomy_heartbeat_polled",
            scopes=self._known_scopes(),
            surfaced=total_proposals,
        )

    # ── Turn Processing ────────────────────────────────────────────────

    async def _process_turn(self, message: ChannelMessage, connection_id: str = "owner") -> str:
        processor = self._get_or_create_turn_processor(connection_id)
        lock = self._connection_locks.setdefault(processor.connection_key, asyncio.Lock())
        async with lock:
            turn_token, session_token = self._activate_turn_processor(processor)
            try:
                return await self._process_turn_with_active_context(message, connection_id)
            finally:
                self._deactivate_turn_processor(turn_token, session_token)

    async def _process_turn_with_active_context(
        self, message: ChannelMessage, connection_id: str,
    ) -> str:
        tc = self._turn_context()
        session_id = self._ensure_session_id()
        scope_id = tc.scope_id
        await self._ensure_persona_state_loaded(scope_id)
        tc.turn_number += 1
        turn_number = tc.turn_number
        turn_id = f"{scope_id}:{turn_number}"

        # Reset taint tracker at turn boundary so prior-turn taint doesn't leak
        taint_tracker = TaintTracker()
        taint_tracker.reset()

        with correlation_scope(turn_id=turn_id, scope_id=scope_id):
            active_gates = self._precompile_active_gates()
            await self._audit(
                "active_gates_precompiled",
                step=0,
                turn_number=turn_number,
                active_gate_count=len(active_gates),
            )
            high_confidence_suggestions = await self._collect_suggestions(connection_id)
            processed_message_text, blocked_response, input_gate_results = await self._run_input_gates(
                active_gates=active_gates,
                message=message,
                connection_id=connection_id,
                turn_number=turn_number,
            )
            if blocked_response is not None:
                await self.channel.send(connection_id, blocked_response, reply_to=message.reply_to)
                await self._audit(
                    "turn_processed",
                    turn_number=turn_number,
                    route="blocked_input_gate",
                )
                return blocked_response

            signed = await self._prepare_signed_inbound_message(
                message=message,
                processed_message_text=processed_message_text,
                turn_number=turn_number,
            )
            # Record inbound taint so tool outputs inherit it via propagation
            taint_tracker.on_tool_input(signed.taint)
            cm = self._get_context_manager()

            chronicle_item = ContextItem(
                ctx_id=f"chronicle:{turn_number}:{uuid.uuid4().hex}",
                zone=ContextZone.chronicle,
                content=f"[{signed.taint.value}] {signed.message.sender_id}: {signed.message.text}",
                token_count=_counter.count(signed.message.text),
                created_at=datetime.now(UTC),
                turn_number=turn_number,
                source=f"channel:{signed.message.channel}",
                taint=signed.taint,
                kind="message",
            )
            if cm is not None:
                cm.add(scope_id, chronicle_item)
            if tc.chronicle_store is not None:
                await tc.chronicle_store.append(scope_id, chronicle_item)

            await self._auto_retrieve_memories(signed.message.text, cm, signed.taint, turn_number)
            await self._ingest_raw_memory(signed.message.text, signed.taint, session_id, turn_number)

            evicted_ctx_ids: list[str] = []
            if cm is not None:
                evicted_ctx_ids = cm.enforce_budget(
                    scope_id,
                    turn_number=turn_number,
                    current_goal=None,
                )
            await self._handle_evicted_context(cm, scope_id, evicted_ctx_ids, session_id, turn_number)
            available_skills = self._available_skill_names()
            await self._audit(
                "skill_availability_checked",
                step=6,
                available_skills=available_skills,
                has_skills=bool(available_skills),
            )
            proxy_toolset, planner_toolset = await self._prepare_agent_toolsets(
                connection_id=connection_id,
                turn_number=turn_number,
            )

            # Why queue-first: queue consumers are the primary execution path.
            # Procedural path is the fallback for when queue infra is unavailable.
            if self._should_use_queue_path():
                return await self._process_turn_via_queue(
                    processed_message_text, turn_id, turn_number,
                    scope_id, cm, tc, connection_id, message,
                    taint_tracker=taint_tracker,
                    high_confidence_suggestions=high_confidence_suggestions,
                    session_id=session_id,
                )

            if tc.proxy is None:
                raise RuntimeError("turn_context.proxy is required")

            rendered_context = ""
            if cm is not None:
                rendered_context = cm.render(scope_id, turn_number)

            routed = await run_structured_agent(
                agent=tc.proxy,
                prompt=self._build_proxy_prompt(
                    processed_message_text,
                    rendered_context,
                    toolset=proxy_toolset,
                ),
                call_name="proxy",
                default_context_profile=self.default_context_profile,
            )
            if not isinstance(routed, RouteDecision):
                raise TypeError("proxy must return RouteDecision")

            interaction_mode = await resolve_interaction_mode(
                route_decision=routed,
                scope_id=scope_id,
                autonomy_calibrator=self._get_autonomy_calibrator(),
                gate_results=input_gate_results,
                personality_engine=tc.personality_engine,
            )
            await self._audit(
                "interaction_mode_resolved",
                turn_number=turn_number,
                interaction_register=routed.interaction_register.value,
                interaction_mode=interaction_mode.value,
                context_profile=routed.context_profile,
            )

            if cm is not None:
                cm.set_profile(scope_id, routed.context_profile)

            response_text = self._route_response_text(routed)
            response_text = await self._handle_planner_route(
                routed,
                response_text,
                connection_id,
                turn_number,
                message.text,
                rendered_context,
                interaction_mode,
                planner_toolset,
            )
            response_text = self._prepend_high_confidence_suggestions(
                response_text,
                high_confidence_suggestions,
            )
            # Stamp response with accumulated taint (lattice-join of all sources
            # touched during this turn): input message + proxy + planner + tools.
            accumulated_taint = taint_tracker.get_current_taint()

            response_text, blocked_gate_names = await self._evaluate_output_gates(
                response_text,
                accumulated_taint,
                message.sender_id,
                turn_number,
            )

            # Step 9 — execute memory queries the agent requested
            await self._process_memory_queries(
                routed.response, accumulated_taint, session_id, scope_id,
                cm, turn_number,
            )

            # Step 10 — execute memory write ops (gated)
            await self._process_memory_ops(
                routed.response, accumulated_taint, session_id, turn_number,
            )

            response_item = ContextItem(
                ctx_id=f"chronicle:{turn_number}:resp:{uuid.uuid4().hex}",
                zone=ContextZone.chronicle,
                content=f"Silas: {response_text}",
                token_count=_counter.count(response_text),
                created_at=datetime.now(UTC),
                turn_number=turn_number,
                source="agent:proxy",
                taint=accumulated_taint,
                kind="message",
            )
            if cm is not None:
                cm.add(scope_id, response_item)
            if tc.chronicle_store is not None:
                await tc.chronicle_store.append(scope_id, response_item)

            # Step 11.5 — ingest agent output as raw memory for future recall
            await self._ingest_raw_memory(
                response_text, accumulated_taint, session_id, turn_number,
            )
            await self.channel.send(connection_id, response_text, reply_to=message.reply_to)
            await self._audit("phase1a_noop", step=14, note="access state updates skipped")
            await self._record_autonomy_outcome(
                turn_number=turn_number,
                route=routed.route,
                blocked=bool(blocked_gate_names),
            )
            await self._audit("turn_processed", turn_number=turn_number, route=routed.route)

            return response_text

    async def _handle_evicted_context(
        self,
        cm: LiveContextManager | None,
        scope_id: str,
        evicted_ctx_ids: list[str],
        session_id: str,
        turn_number: int,
    ) -> None:
        """Persist evicted context items and audit. Extracted for C901 budget."""
        if evicted_ctx_ids:
            evicted_items = self._take_evicted_context_items(cm, scope_id)
            await self._persist_evicted_context(evicted_items, session_id, turn_number)
            await self._audit(
                "context_budget_enforced",
                step=5,
                turn_number=turn_number,
                evicted_ctx_ids=evicted_ctx_ids,
            )

    async def _process_turn_via_queue(
        self,
        message_text: str,
        turn_id: str,
        turn_number: int,
        scope_id: str,
        cm: LiveContextManager | None,
        tc: TurnContext,
        connection_id: str,
        message: ChannelMessage,
        taint_tracker: object | None = None,
        high_confidence_suggestions: list[SuggestionProposal] | None = None,
        session_id: str = "",
    ) -> str:
        """Dispatch a turn through the queue bridge instead of direct agent calls."""
        assert self.queue_bridge is not None  # caller guarantees this
        personality_directives = await self._queue_personality_directives(
            scope_id=scope_id,
            message=message,
        )
        rendered_context = ""
        if cm is not None:
            rendered_context = cm.render(scope_id, turn_number)
        await self.queue_bridge.dispatch_turn(
            user_message=message_text,
            trace_id=turn_id,
            metadata={
                "personality_directives": personality_directives,
                "rendered_context_json": json.dumps({"rendered_context": rendered_context}),
            },
        )
        queue_response = await self.queue_bridge.collect_response(
            trace_id=turn_id, timeout_s=self._queue_timeout_seconds(),
        )
        queue_text = ""
        if queue_response is not None:
            queue_text = str(queue_response.payload.get("text", ""))
        if not queue_text:
            queue_text = "Processing your request through the queue system."

        # Step 13 (partial) — prepend high-confidence suggestions
        if high_confidence_suggestions:
            queue_text = self._prepend_high_confidence_suggestions(
                queue_text, high_confidence_suggestions,
            )

        accumulated_taint = TaintLevel.owner
        if taint_tracker is not None and hasattr(taint_tracker, "get_current_taint"):
            accumulated_taint = taint_tracker.get_current_taint()

        # Step 8 — output gates (security boundary)
        blocked_gate_names: list[str] = []
        queue_text, blocked_gate_names = await self._evaluate_output_gates(
            queue_text, accumulated_taint, message.sender_id, turn_number,
        )

        agent_response = None
        if queue_response is not None:
            agent_response = queue_response.payload.get("agent_response")
        await self._process_memory_queries(
            agent_response, accumulated_taint, session_id, scope_id,
            cm, turn_number,
        )

        await self._process_memory_ops(
            agent_response, accumulated_taint, session_id, turn_number,
        )

        response_item = ContextItem(
            ctx_id=f"chronicle:{turn_number}:resp:{uuid.uuid4().hex}",
            zone=ContextZone.chronicle,
            content=f"Silas: {queue_text}",
            token_count=_counter.count(queue_text),
            created_at=datetime.now(UTC),
            turn_number=turn_number,
            source="agent:queue_bridge",
            taint=accumulated_taint,
            kind="message",
        )
        if cm is not None:
            cm.add(scope_id, response_item)
        if tc.chronicle_store is not None:
            await tc.chronicle_store.append(scope_id, response_item)

        await self._ingest_raw_memory(
            queue_text, accumulated_taint, session_id, turn_number,
        )

        await self.channel.send(connection_id, queue_text, reply_to=message.reply_to)

        await self._audit("phase1a_noop", step=14, note="access state updates skipped")

        await self._record_autonomy_outcome(
            turn_number=turn_number,
            route="queue_bridge",
            blocked=bool(blocked_gate_names),
        )

        await self._audit("turn_processed", turn_number=turn_number, route="queue_bridge")
        return queue_text

    # ── Suggestions & Proactivity ──────────────────────────────────────

    async def _collect_suggestions(self, connection_id: str) -> list[SuggestionProposal]:
        engine = self._get_suggestion_engine()
        if engine is None:
            await self._audit("phase1a_noop", step=0.5, note="review/proactive queue skipped")
            return []

        scope_id = self._turn_context().scope_id
        idle = await engine.generate_idle(scope_id, datetime.now(UTC))
        high = [s for s in idle if s.confidence > 0.80]
        low = [s for s in idle if s.confidence <= 0.80]
        for suggestion in low:
            await self._push_suggestion_to_side_panel(connection_id, suggestion)
        await self._audit("proactive_queue_reviewed", step=0.5, surfaced=len(idle), high_confidence=len(high), side_panel=len(low))
        return high

    async def _push_suggestion_to_side_panel(self, connection_id: str, suggestion: SuggestionProposal) -> None:
        send_suggestion = getattr(self.channel, "send_suggestion", None)
        if callable(send_suggestion):
            await send_suggestion(connection_id, suggestion)
            await self._audit("suggestion_side_panel_enqueued", suggestion_id=suggestion.id, confidence=suggestion.confidence, source=suggestion.source, category=suggestion.category)
            return
        await self._audit("suggestion_side_panel_unavailable", suggestion_id=suggestion.id, confidence=suggestion.confidence, source=suggestion.source, category=suggestion.category)

    async def _record_autonomy_outcome(self, *, turn_number: int, route: str, blocked: bool) -> None:
        calibrator = self._get_autonomy_calibrator()
        if calibrator is None:
            await self._audit("phase1a_noop", step=15, note="personality/autonomy post-turn updates skipped")
            return
        outcome = "declined" if blocked else "approved"
        await calibrator.record_outcome(self._turn_context().scope_id, action_family=route, outcome=outcome)
        await self._audit("autonomy_calibration_recorded", step=15, turn_number=turn_number, action_family=route, outcome=outcome)

    # ── Queue Infrastructure ───────────────────────────────────────────

    def _should_use_queue_path(self) -> bool:
        if self.queue_bridge is None:
            return False

        use_queue = self._config_value("execution", "use_queue_path", default=True)
        if use_queue is False or use_queue == 0:
            return False

        orchestrator = self.queue_bridge.orchestrator
        if not orchestrator.running:
            logger.warning(
                "Queue orchestrator not running — falling back to procedural path"
            )
            return False

        return True

    async def _start_queue_orchestrator(self) -> None:
        if self.queue_bridge is None:
            return
        try:
            await self.queue_bridge.orchestrator.start()
            await self._audit("queue_orchestrator_started")
        except Exception as exc:
            await self._audit(
                "queue_orchestrator_start_failed", error=str(exc),
            )

    async def _stop_queue_orchestrator(self) -> None:
        if self.queue_bridge is None:
            return
        try:
            await self.queue_bridge.orchestrator.stop()
            await self._audit("queue_orchestrator_stopped")
        except Exception as exc:
            await self._audit(
                "queue_orchestrator_stop_failed", error=str(exc),
            )

    async def _on_approval_response(self, token_id: str, verdict: ApprovalVerdict, resolved_by: str) -> None:
        resolved = await self._approval_flow.handle_response(token_id, verdict, resolved_by)
        if resolved:
            await self._audit("approval_resolved", token_id=token_id, verdict=verdict.value, resolved_by=resolved_by)
        else:
            await self._audit("approval_response_ignored", token_id=token_id, verdict=verdict.value, resolved_by=resolved_by)
