"""The Stream — Silas's permanent orchestration session.

Core turn-processing loop. Plan execution and approval flow are
delegated to silas.core.plan_executor and silas.core.approval_flow
to keep this file focused on orchestration.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import uuid
from collections.abc import Awaitable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from silas.agents.structured import run_structured_agent
from silas.core.approval_flow import ApprovalFlow
from silas.core.context_manager import LiveContextManager
from silas.core.interaction_mode import resolve_interaction_mode
from silas.core.logging import correlation_scope
from silas.core.plan_executor import (
    build_skill_work_item,
    execute_plan_actions,
    extract_skill_inputs,
    extract_skill_name,
    plan_action_to_work_item,
    resolve_work_item_approval,
)
from silas.core.plan_parser import MarkdownPlanParser
from silas.core.token_counter import HeuristicTokenCounter
from silas.core.turn_context import TurnContext
from silas.gates import SilasGateRunner
from silas.memory.retriever import SilasMemoryRetriever
from silas.models.agents import (
    AgentResponse,
    InteractionMode,
    MemoryOp,
    MemoryOpType,
    PlanAction,
    RouteDecision,
)
from silas.models.approval import ApprovalScope, ApprovalToken, ApprovalVerdict
from silas.models.connections import ConnectionFailure
from silas.models.context import ContextItem, ContextSubscription, ContextZone
from silas.models.gates import Gate, GateResult, GateTrigger
from silas.models.memory import MemoryItem, MemoryType, ReingestionTier
from silas.models.messages import (
    ChannelMessage,
    SignedMessage,
    TaintLevel,
)
from silas.models.proactivity import SuggestionProposal
from silas.models.work import WorkItem, WorkItemStatus, WorkItemType
from silas.protocols.approval import NonceStore
from silas.protocols.channels import ChannelAdapterCore
from silas.protocols.connections import ConnectionManager
from silas.protocols.memory import MemoryStore
from silas.protocols.proactivity import AutonomyCalibrator, SuggestionEngine
from silas.protocols.scheduler import TaskScheduler
from silas.protocols.skills import SkillResolver
from silas.protocols.work import PlanParser, WorkItemStore
from silas.security.taint import TaintTracker
from silas.tools.approval_required import ApprovalRequiredToolset
from silas.tools.filtered import FilteredToolset
from silas.tools.prepared import PreparedToolset
from silas.tools.skill_toolset import SkillToolset, ToolDefinition

if TYPE_CHECKING:
    from silas.queue.bridge import QueueBridge

_counter = HeuristicTokenCounter()
_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_:-]+)")
_IN_PROGRESS_STATUSES: tuple[WorkItemStatus, ...] = (
    WorkItemStatus.pending,
    WorkItemStatus.running,
    WorkItemStatus.healthy,
    WorkItemStatus.stuck,
    WorkItemStatus.paused,
)
_PROXY_BASE_TOOLS: tuple[tuple[str, str], ...] = (
    ("context_inspect", "Inspect active turn context for routing."),
    ("memory_search", "Retrieve relevant memories before routing."),
    ("tell_user", "Send interim status updates to the user."),
    ("web_search", "Look up current external information."),
)
_PLANNER_BASE_TOOLS: tuple[tuple[str, str], ...] = (
    ("memory_search", "Retrieve historical context before planning."),
    ("request_research", "Delegate research to executor queue."),
    ("validate_plan", "Validate markdown plan structure."),
    ("web_search", "Look up supporting facts for plan quality."),
)

logger = logging.getLogger(__name__)


class _InMemoryNonceStore:
    """Ephemeral replay guard for local/test streams.

    Why: production stream startup injects ``SQLiteNonceStore``. This fallback keeps
    direct unit-test ``Stream(...)`` construction replay-safe without requiring a DB.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def is_used(self, domain: str, nonce: str) -> bool:
        return f"{domain}:{nonce}" in self._seen

    async def record(self, domain: str, nonce: str) -> None:
        self._seen.add(f"{domain}:{nonce}")

    async def prune_expired(self, older_than: datetime) -> int:
        del older_than
        return 0


@dataclass(slots=True)
class TurnProcessor:
    """Per-connection turn processor state container.

    Why: Stream needs isolated mutable turn state by connection to prevent
    chronicle/memory/workspace leakage across concurrent customer sessions.
    """

    connection_key: str
    turn_context: TurnContext
    session_id: str


@dataclass(slots=True)
class Stream:
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

    async def _rehydrate(self) -> None:
        """Restore state from previous run (spec §5.1.3)."""
        tc = self.turn_context
        cm = self._get_context_manager()
        session_id = self._ensure_session_id()
        known_scopes = self._known_scopes()

        await self._rehydrate_system_zone(cm, known_scopes)
        await self._rehydrate_chronicle(tc, cm, known_scopes)
        await self._rehydrate_all_scope_memories(tc, cm, session_id, known_scopes)

        in_progress_items = await self._list_in_progress_work_items()
        await self._restore_context_subscriptions(cm, in_progress_items)
        await self._add_rehydration_system_message(cm, known_scopes)
        await self._resume_in_progress_work_items(in_progress_items)

        # Persona state is intentionally loaded on demand to avoid eager scope fan-out.
        self._pending_persona_scopes = set(known_scopes)
        await self._rehydrate_pending_proposals(known_scopes)

        await self._audit(
            "stream_rehydrated",
            turn_number=tc.turn_number,
            scopes=known_scopes,
        )

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

    async def _rehydrate_system_zone(
        self,
        cm: LiveContextManager | None,
        scopes: list[str],
    ) -> None:
        if cm is None:
            await self._audit("stream_startup_dependency_missing", dependency="context_manager")
            return

        constitution_content = self._constitution_content()
        tools_content = self._tool_descriptions_content()
        config_content = self._configuration_content()

        for scope_id in scopes:
            self._replace_context_item(
                cm,
                scope_id,
                ContextItem(
                    ctx_id="system:constitution",
                    zone=ContextZone.system,
                    content=constitution_content,
                    token_count=_counter.count(constitution_content),
                    created_at=datetime.now(UTC),
                    turn_number=self._turn_context().turn_number,
                    source="system:constitution",
                    taint=TaintLevel.owner,
                    kind="system",
                    pinned=True,
                ),
            )
            self._replace_context_item(
                cm,
                scope_id,
                ContextItem(
                    ctx_id="system:tools",
                    zone=ContextZone.system,
                    content=tools_content,
                    token_count=_counter.count(tools_content),
                    created_at=datetime.now(UTC),
                    turn_number=self._turn_context().turn_number,
                    source="system:tools",
                    taint=TaintLevel.owner,
                    kind="system",
                    pinned=True,
                ),
            )
            self._replace_context_item(
                cm,
                scope_id,
                ContextItem(
                    ctx_id="system:configuration",
                    zone=ContextZone.system,
                    content=config_content,
                    token_count=_counter.count(config_content),
                    created_at=datetime.now(UTC),
                    turn_number=self._turn_context().turn_number,
                    source="system:configuration",
                    taint=TaintLevel.owner,
                    kind="system",
                    pinned=True,
                ),
            )

    async def _rehydrate_chronicle(
        self,
        tc: TurnContext,
        cm: LiveContextManager | None,
        scopes: list[str],
    ) -> None:
        if cm is None:
            await self._audit("stream_startup_dependency_missing", dependency="context_manager")
            return
        if tc.chronicle_store is None:
            await self._audit("stream_startup_dependency_missing", dependency="chronicle_store")
            return

        max_entries = self._rehydration_max_chronicle_entries()
        mask_after_turns = self._observation_mask_after_turns()
        max_seen_turn = tc.turn_number

        for scope_id in scopes:
            recent = await tc.chronicle_store.get_recent(scope_id, limit=max_entries)
            scope_latest_turn = max_seen_turn
            if recent:
                scope_latest_turn = max(item.turn_number for item in recent)
                max_seen_turn = max(max_seen_turn, scope_latest_turn)
            for item in recent:
                hydrated = self._masked_if_stale(item, scope_latest_turn, mask_after_turns)
                cm.add(scope_id, hydrated.model_copy(update={"kind": "message"}))

        tc.turn_number = max_seen_turn

    async def _rehydrate_all_scope_memories(
        self,
        tc: TurnContext,
        cm: LiveContextManager | None,
        session_id: str,
        scopes: list[str],
    ) -> None:
        if cm is None:
            await self._audit("stream_startup_dependency_missing", dependency="context_manager")
            return
        if tc.memory_store is None:
            await self._audit("stream_startup_dependency_missing", dependency="memory_store")
            return

        for scope_id in scopes:
            include_session_memories = scope_id == tc.scope_id
            await self._rehydrate_memories(
                tc,
                cm,
                session_id,
                scope_id=scope_id,
                include_session_memories=include_session_memories,
            )

    async def _list_in_progress_work_items(self) -> list[WorkItem]:
        work_item_store = self.work_item_store
        if work_item_store is None:
            await self._audit("stream_startup_dependency_missing", dependency="work_item_store")
            return []

        calls: list[Awaitable[list[WorkItem]]] = [
            work_item_store.list_by_status(status) for status in _IN_PROGRESS_STATUSES
        ]
        call_results = await asyncio.gather(*calls, return_exceptions=True)

        items_by_id: dict[str, WorkItem] = {}
        for call_result in call_results:
            if isinstance(call_result, (RuntimeError, ValueError, OSError)):
                await self._audit("work_item_rehydrate_query_failed", error=str(call_result))
                continue
            if isinstance(call_result, BaseException):
                raise call_result

            for item in call_result:
                items_by_id[item.id] = item
        return list(items_by_id.values())

    async def _restore_context_subscriptions(
        self,
        cm: LiveContextManager | None,
        in_progress_items: list[WorkItem],
    ) -> None:
        if cm is None:
            await self._audit("stream_startup_dependency_missing", dependency="context_manager")
            return
        if not in_progress_items:
            return

        restored = 0
        scope_id = self._turn_context().scope_id
        for item in in_progress_items:
            for target in self._file_subscription_targets(item):
                subscription = ContextSubscription(
                    sub_id=f"rehydrate:{item.id}:{hashlib.sha256(target.encode('utf-8')).hexdigest()[:12]}",
                    sub_type="file",
                    target=target,
                    zone=ContextZone.workspace,
                    created_at=datetime.now(UTC),
                    turn_created=self._turn_context().turn_number,
                    content_hash=hashlib.sha256(target.encode("utf-8")).hexdigest(),
                    active=True,
                    token_count=0,
                )
                cm.subscribe(scope_id, subscription)
                restored += 1

        await self._audit("context_subscriptions_restored", restored=restored)

    async def _add_rehydration_system_message(
        self,
        cm: LiveContextManager | None,
        scopes: list[str],
    ) -> None:
        if cm is None:
            await self._audit("stream_startup_dependency_missing", dependency="context_manager")
            return

        content = "[SYSTEM] Session rehydrated after restart."
        for scope_id in scopes:
            item = ContextItem(
                ctx_id=f"system:rehydrated:{scope_id}",
                zone=ContextZone.chronicle,
                content=content,
                token_count=_counter.count(content),
                created_at=datetime.now(UTC),
                turn_number=self._turn_context().turn_number,
                source="system:rehydrate",
                taint=TaintLevel.owner,
                kind="message",
            )
            cm.add(scope_id, item)
            chronicle_store = self._turn_context().chronicle_store
            if chronicle_store is not None:
                await chronicle_store.append(scope_id, item)

    async def _resume_in_progress_work_items(
        self, in_progress_items: list[WorkItem],
    ) -> None:
        if not in_progress_items:
            return

        executor = self._turn_context().work_executor
        if executor is None:
            await self._audit("stream_startup_dependency_missing", dependency="work_executor")
            return

        calls = [executor.execute(item) for item in in_progress_items]
        call_results = await asyncio.gather(*calls, return_exceptions=True)
        resumed = 0
        for item, call_result in zip(in_progress_items, call_results, strict=False):
            if isinstance(call_result, (RuntimeError, ValueError, OSError)):
                await self._audit(
                    "work_item_resume_failed",
                    work_item_id=item.id,
                    error=str(call_result),
                )
                continue
            if isinstance(call_result, BaseException):
                raise call_result
            resumed += 1

        await self._audit("work_items_resumed", resumed=resumed)

    async def _rehydrate_pending_proposals(self, scopes: list[str]) -> None:
        if not scopes:
            return

        await self._rehydrate_pending_batch_reviews(scopes)
        await self._rehydrate_pending_suggestions(scopes)
        await self._rehydrate_pending_autonomy_proposals(scopes)

    async def _rehydrate_pending_batch_reviews(self, scopes: list[str]) -> None:
        work_item_store = self.work_item_store
        send_batch_review = getattr(self.channel, "send_batch_review", None)
        if work_item_store is None or not callable(send_batch_review):
            return

        total = 0
        for scope_id in scopes:
            batches = await self._load_store_pending_items(
                work_item_store,
                "list_pending_batch_reviews",
                scope_id,
            )
            for batch in batches:
                await send_batch_review(self.owner_id, batch)
                total += 1
        if total:
            await self._audit("pending_batch_reviews_rehydrated", total=total)

    async def _rehydrate_pending_suggestions(self, scopes: list[str]) -> None:
        work_item_store = self.work_item_store
        send_suggestion = getattr(self.channel, "send_suggestion", None)
        if work_item_store is not None and callable(send_suggestion):
            total = 0
            for scope_id in scopes:
                pending = await self._load_store_pending_items(
                    work_item_store,
                    "list_pending_suggestions",
                    scope_id,
                )
                for suggestion in pending:
                    await send_suggestion(self.owner_id, suggestion)
                    total += 1
            if total:
                await self._audit("pending_suggestions_rehydrated", total=total)
                return

        suggestion_engine = self._get_suggestion_engine()
        if suggestion_engine is None:
            return

        total = 0
        now = datetime.now(UTC)
        for scope_id in scopes:
            pending = await suggestion_engine.generate_idle(scope_id, now)
            for suggestion in pending:
                await self._push_suggestion_to_side_panel(self.owner_id, suggestion)
                total += 1
        if total:
            await self._audit("pending_suggestions_rehydrated", total=total)

    async def _rehydrate_pending_autonomy_proposals(self, scopes: list[str]) -> None:
        work_item_store = self.work_item_store
        send_review = getattr(self.channel, "send_autonomy_threshold_review", None)
        if work_item_store is not None and callable(send_review):
            total = 0
            for scope_id in scopes:
                pending = await self._load_store_pending_items(
                    work_item_store,
                    "list_pending_autonomy_proposals",
                    scope_id,
                )
                for proposal in pending:
                    await send_review(self.owner_id, proposal)
                    total += 1
            if total:
                await self._audit("pending_autonomy_proposals_rehydrated", total=total)
                return

        autonomy_calibrator = self._get_autonomy_calibrator()
        if autonomy_calibrator is None or not callable(send_review):
            return

        total = 0
        now = datetime.now(UTC)
        for scope_id in scopes:
            pending = await autonomy_calibrator.evaluate(scope_id, now)
            for proposal in pending:
                await send_review(self.owner_id, proposal)
                total += 1
        if total:
            await self._audit("pending_autonomy_proposals_rehydrated", total=total)

    async def _load_store_pending_items(
        self,
        work_item_store: WorkItemStore,
        method_name: str,
        scope_id: str,
    ) -> list[object]:
        method = getattr(work_item_store, method_name, None)
        if not callable(method):
            return []

        try:
            maybe_result = method(scope_id)
        except TypeError:
            maybe_result = method()
        if not isinstance(maybe_result, Awaitable):
            return []

        result = await maybe_result
        if isinstance(result, list):
            return result
        return []

    async def _rehydrate_memories(
        self,
        tc: TurnContext,
        cm: LiveContextManager,
        session_id: str,
        *,
        scope_id: str,
        include_session_memories: bool,
    ) -> None:
        """Load profile state and recent session context for startup continuity."""
        profile_items = await tc.memory_store.search_keyword("user profile preferences", limit=1)
        for item in profile_items:
            cm.add(
                scope_id,
                ContextItem(
                    ctx_id=f"memory:profile:{item.memory_id}",
                    zone=ContextZone.memory,
                    content=item.content,
                    token_count=_counter.count(item.content),
                    created_at=datetime.now(UTC),
                    turn_number=tc.turn_number,
                    source="memory:profile",
                    taint=item.taint,
                    kind="memory",
                    pinned=True,
                ),
            )

        if not include_session_memories:
            return

        recent_session = await tc.memory_store.search_session(session_id)
        for item in recent_session[:10]:
            cm.add(
                scope_id,
                ContextItem(
                    ctx_id=f"memory:session:{item.memory_id}",
                    zone=ContextZone.memory,
                    content=item.content,
                    token_count=_counter.count(item.content),
                    created_at=datetime.now(UTC),
                    turn_number=tc.turn_number,
                    source="memory:session_rehydrate",
                    taint=item.taint,
                    kind="memory",
                ),
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

    async def verify_inbound(self, signed_message: SignedMessage) -> tuple[bool, str]:
        """Check whether an inbound message is trustworthy.

        Trust model: The channel layer is responsible for authenticating senders
        (e.g. validating a WebSocket session token). The stream does NOT self-sign
        messages — that would be circular (signing and verifying with the same key
        always passes). Instead we check:

        1. Did the channel authenticate this sender? (is_authenticated flag)
        2. If a client-side signature is present (future), verify it with the
           client's public key and consume the nonce for replay protection.

        When client-side signing is added, this method will also verify Ed25519
        signatures against registered client public keys — not the stream's own key.
        """
        msg = signed_message.message

        # Future: if signature is non-empty, verify against client public key + nonce.
        # For now, trust depends entirely on channel authentication.
        if msg.is_authenticated:
            return True, "authenticated_session"

        return False, "no_client_signature"

    async def _prepare_signed_inbound_message(
        self,
        *,
        message: ChannelMessage,
        processed_message_text: str,
        turn_number: int,
    ) -> SignedMessage:
        """Build a SignedMessage using channel-level trust, not self-signing.

        The old approach signed messages with the stream's own key then verified
        with the same key — a tautology that always passed. Now trust derives from
        the channel's authentication state (set before the message reaches the stream).
        """
        signed = self._create_inbound_signed_message(message, processed_message_text)
        is_verified, verify_reason = await self.verify_inbound(signed)
        if not is_verified:
            await self._audit(
                "inbound_message_untrusted",
                turn_number=turn_number,
                sender_id=message.sender_id,
                reason=verify_reason,
            )
        taint = self._resolve_inbound_taint(message.sender_id, is_verified)
        return signed.model_copy(update={"taint": taint})

    def _create_inbound_signed_message(
        self,
        message: ChannelMessage,
        processed_message_text: str,
    ) -> SignedMessage:
        """Wrap an inbound message without self-signing.

        Signature is empty — the stream must not sign-then-verify its own messages.
        When client-side crypto is added, the client will provide the signature and
        nonce; the stream will only verify. The Ed25519 key infrastructure is kept
        for that future use and for signing outbound attestations.
        """
        message_payload = message.model_copy(update={"text": processed_message_text})
        return SignedMessage(
            message=message_payload,
            signature=b"",
            nonce=uuid.uuid4().hex,
            taint=TaintLevel.external,
        )

    def _sign_payload(self, canonical_payload: bytes) -> bytes:
        """Sign a payload with the stream's key (used for outbound attestations, not inbound)."""
        signing_key = self._signing_key
        if isinstance(signing_key, Ed25519PrivateKey):
            return signing_key.sign(canonical_payload)
        if isinstance(signing_key, bytes):
            return hmac.new(signing_key, canonical_payload, hashlib.sha256).digest()
        raise RuntimeError("stream signing key is not configured")

    def _is_valid_signature(self, canonical_payload: bytes, signature: bytes) -> bool:
        """Verify a signature against the stream's key (kept for outbound/future client use)."""
        signing_key = self._signing_key
        if isinstance(signing_key, Ed25519PrivateKey):
            try:
                signing_key.public_key().verify(signature, canonical_payload)
            except (InvalidSignature, TypeError, ValueError):
                return False
            return True
        if isinstance(signing_key, bytes):
            expected_signature = hmac.new(signing_key, canonical_payload, hashlib.sha256).digest()
            return hmac.compare_digest(expected_signature, signature)
        return False

    def _resolve_inbound_taint(self, sender_id: str, is_verified: bool) -> TaintLevel:
        """Determine trust level from channel authentication and sender identity.

        Owner taint requires BOTH: the channel authenticated the sender AND the
        sender_id matches the stream owner. This prevents privilege escalation
        from authenticated-but-non-owner users.
        """
        if is_verified and sender_id == self.owner_id:
            return TaintLevel.owner
        return TaintLevel.external

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
    ) -> str:
        """Dispatch a turn through the queue bridge instead of direct agent calls.

        Why a separate method: keeps _process_turn_with_active_context under
        C901 complexity limit while isolating the queue integration path.
        """
        assert self.queue_bridge is not None  # caller guarantees this
        await self.queue_bridge.dispatch_turn(
            user_message=message_text, trace_id=turn_id,
        )
        queue_response = await self.queue_bridge.collect_response(
            trace_id=turn_id, timeout_s=30.0,
        )
        queue_text = ""
        if queue_response is not None:
            queue_text = str(queue_response.payload.get("text", ""))
        if not queue_text:
            queue_text = "Processing your request through the queue system."

        response_item = ContextItem(
            ctx_id=f"chronicle:{turn_number}:resp:{uuid.uuid4().hex}",
            zone=ContextZone.chronicle,
            content=f"Silas: {queue_text}",
            token_count=_counter.count(queue_text),
            created_at=datetime.now(UTC),
            turn_number=turn_number,
            source="agent:queue_bridge",
            taint=TaintLevel.owner,
            kind="message",
        )
        if cm is not None:
            cm.add(scope_id, response_item)
        if tc.chronicle_store is not None:
            await tc.chronicle_store.append(scope_id, response_item)

        await self.channel.send(connection_id, queue_text, reply_to=message.reply_to)
        await self._audit("turn_processed", turn_number=turn_number, route="queue_bridge")
        return queue_text

    # ── Planner Route Handling ─────────────────────────────────────────

    async def _handle_planner_route(
        self,
        routed: RouteDecision,
        response_text: str,
        connection_id: str,
        turn_number: int,
        message_text: str,
        rendered_context: str,
        interaction_mode: InteractionMode,
        planner_toolset: ApprovalRequiredToolset | None,
    ) -> str:
        plan_flow_payload: dict[str, object] = {
            "actions_seen": 0, "skills_executed": 0, "skills_skipped": 0,
            "approval_requested": 0, "approval_approved": 0, "approval_declined": 0,
        }
        if routed.route == "planner":
            plan_actions, planner_message = await self._resolve_plan_actions(
                routed=routed,
                message_text=message_text,
                rendered_context=rendered_context,
                turn_number=turn_number,
                planner_toolset=planner_toolset,
            )
            plan_flow_payload["actions_seen"] = len(plan_actions)
            if plan_actions:
                continuation_of = routed.continuation_of
                for action in plan_actions:
                    raw = action.get("continuation_of")
                    if isinstance(raw, str) and raw.strip():
                        continuation_of = raw
                        break
                work_exec_summary = await self._try_work_execution(
                    plan_actions,
                    turn_number,
                    continuation_of,
                    interaction_mode,
                    connection_id,
                )
                if work_exec_summary is not None:
                    response_text = work_exec_summary
                else:
                    response_text, plan_flow_payload = await self._execute_planner_skill_actions(
                        plan_actions=plan_actions,
                        connection_id=connection_id,
                        turn_number=turn_number,
                        fallback_response=response_text,
                        interaction_mode=interaction_mode,
                    )
            else:
                if planner_message:
                    response_text = planner_message
                await self._audit(
                    "planner_stub_used",
                    turn_number=turn_number,
                    reason=routed.reason,
                )
        await self._audit("plan_approval_flow_checked", step=12, turn_number=turn_number, **plan_flow_payload)
        return response_text

    def _precompile_active_gates(self) -> tuple[Gate, ...]:
        system_gates = self._load_system_gates()
        gate_runner = self._turn_context().gate_runner
        if gate_runner is None:
            return tuple(system_gates)

        precompile = getattr(gate_runner, "precompile_turn_gates", None)
        if not callable(precompile):
            return tuple(system_gates)

        compiled = precompile(system_gates=system_gates)
        return tuple(compiled)

    def _load_system_gates(self) -> list[Gate]:
        config = self._turn_context().config
        if config is None:
            return []

        # Current runtime config shape uses top-level output_gates; if
        # config.gates.system is introduced, it is merged below.
        raw_from_output = getattr(config, "output_gates", None)
        output_gates: list[Gate] = []
        if isinstance(raw_from_output, list):
            for gate in raw_from_output:
                if isinstance(gate, Gate):
                    output_gates.append(gate.model_copy(deep=True))

        raw_gates = getattr(config, "gates", None)
        if raw_gates is None:
            return output_gates

        raw_system = getattr(raw_gates, "system", None)
        if not isinstance(raw_system, list):
            return output_gates

        merged = list(output_gates)
        for gate in raw_system:
            if isinstance(gate, Gate):
                merged.append(gate.model_copy(deep=True))
        return merged

    async def _run_input_gates(
        self,
        *,
        active_gates: tuple[Gate, ...],
        message: ChannelMessage,
        connection_id: str,
        turn_number: int,
    ) -> tuple[str, str | None, list[GateResult]]:
        gate_runner = self._turn_context().gate_runner
        if gate_runner is None or not active_gates:
            await self._audit(
                "input_gates_evaluated",
                step=1,
                turn_number=turn_number,
                policy_results=[],
                quality_results=[],
                configured=bool(active_gates),
            )
            return message.text, None, []

        policy_results, quality_results, merged_context = await gate_runner.check_gates(
            gates=list(active_gates),
            trigger=GateTrigger.every_user_message,
            context={
                "message": message.text,
                "sender_id": message.sender_id,
            },
        )
        policy_payload = [result.model_dump(mode="json") for result in policy_results]
        quality_payload = [result.model_dump(mode="json") for result in quality_results]
        await self._audit(
            "input_gates_evaluated",
            step=1,
            turn_number=turn_number,
            policy_results=policy_payload,
            quality_results=quality_payload,
            configured=True,
        )
        if quality_payload:
            await self._audit(
                "quality_gate_input",
                turn_number=turn_number,
                results=quality_payload,
            )

        for result in policy_results:
            if result.action == "continue":
                continue

            if result.action == "require_approval":
                approved = await self._request_input_gate_approval(
                    result=result,
                    message=message,
                    connection_id=connection_id,
                    turn_number=turn_number,
                )
                if approved:
                    await self._audit(
                        "input_gate_approval_granted",
                        turn_number=turn_number,
                        gate_name=result.gate_name,
                    )
                    continue
                await self._audit(
                    "input_gate_approval_declined",
                    turn_number=turn_number,
                    gate_name=result.gate_name,
                    reason=result.reason,
                )
                blocked = self._input_gate_block_response(merged_context, result)
                return message.text, blocked, policy_results

            if result.action == "block":
                await self._audit(
                    "input_gate_blocked",
                    turn_number=turn_number,
                    gate_name=result.gate_name,
                    reason=result.reason,
                )
                blocked = self._input_gate_block_response(merged_context, result)
                return message.text, blocked, policy_results

        rewritten = merged_context.get("message")
        if isinstance(rewritten, str) and rewritten.strip():
            return rewritten, None, policy_results
        return message.text, None, policy_results

    def _input_gate_block_response(
        self,
        merged_context: dict[str, object],
        result: GateResult,
    ) -> str:
        response = merged_context.get("response")
        if isinstance(response, str) and response.strip():
            return response
        return f"Request blocked by gate '{result.gate_name}': {result.reason}"

    async def _request_input_gate_approval(
        self,
        *,
        result: GateResult,
        message: ChannelMessage,
        connection_id: str,
        turn_number: int,
    ) -> bool:
        approval_flow = self._approval_flow
        if approval_flow is None:
            return False

        work_item = WorkItem(
            id=f"input-gate:{turn_number}:{uuid.uuid4().hex}",
            type=WorkItemType.task,
            title=f"Input gate approval for {result.gate_name}",
            body=message.text,
        )
        decision, token = await approval_flow.request_skill_approval(
            work_item=work_item,
            scope=ApprovalScope.full_plan,
            skill_name=result.gate_name,
            connection_id=connection_id,
        )
        if decision is None or token is None:
            return False
        return decision.verdict == ApprovalVerdict.approved

    async def _resolve_plan_actions(
        self,
        *,
        routed: RouteDecision,
        message_text: str,
        rendered_context: str,
        turn_number: int,
        planner_toolset: ApprovalRequiredToolset | None,
    ) -> tuple[list[dict[str, object]], str | None]:
        planner = self._turn_context().planner
        if planner is None:
            await self._audit("planner_handoff_missing", turn_number=turn_number)
            return [], None

        planner_output = await run_structured_agent(
            agent=planner,
            prompt=self._build_planner_prompt(
                message_text,
                rendered_context,
                toolset=planner_toolset,
            ),
            call_name="planner",
            default_context_profile="planning",
        )
        actions, planner_message = self._extract_plan_actions_from_planner_output(
            planner_output
        )
        await self._audit(
            "planner_handoff_invoked",
            turn_number=turn_number,
            output_type=type(planner_output).__name__,
            actions=len(actions),
        )
        if actions:
            return actions, planner_message

        # Legacy fallback for compatibility while planner output contracts converge.
        fallback_actions = self._extract_plan_actions(routed)
        if fallback_actions:
            await self._audit(
                "planner_handoff_fallback_proxy_actions",
                turn_number=turn_number,
                actions=len(fallback_actions),
            )
        return fallback_actions, planner_message

    def _extract_plan_actions_from_planner_output(
        self, planner_output: object,
    ) -> tuple[list[dict[str, object]], str | None]:
        if isinstance(planner_output, RouteDecision):
            planner_message = None
            if planner_output.response is not None:
                planner_message = planner_output.response.message
            return self._extract_plan_actions(planner_output), planner_message

        if isinstance(planner_output, AgentResponse):
            return self._extract_plan_actions_from_agent_response(planner_output), planner_output.message

        try:
            response = AgentResponse.model_validate(planner_output)
        except ValidationError:
            return [], None
        return self._extract_plan_actions_from_agent_response(response), response.message

    def _extract_plan_actions_from_agent_response(
        self, response: AgentResponse,
    ) -> list[dict[str, object]]:
        plan_action = response.plan_action
        if plan_action is None:
            return []

        action_payload: dict[str, object] = {
            "action": plan_action.action.value,
        }
        if plan_action.plan_markdown:
            action_payload["plan_markdown"] = plan_action.plan_markdown
        if plan_action.continuation_of:
            action_payload["continuation_of"] = plan_action.continuation_of
        if plan_action.interaction_mode_override is not None:
            action_payload["interaction_mode_override"] = (
                plan_action.interaction_mode_override.value
            )
        return [action_payload]

    async def _try_work_execution(
        self,
        plan_actions: list[dict[str, object]],
        turn_number: int,
        continuation_of: str | None,
        interaction_mode: InteractionMode,
        connection_id: str,
    ) -> str | None:
        """Try executing plan actions via work executor. Returns summary or None."""
        executor = self._turn_context().work_executor
        if executor is None:
            return None

        plan_actions_with_mode = [
            {**action, "interaction_mode": interaction_mode.value}
            for action in plan_actions
        ]
        approved_actions = await self._ensure_plan_action_approvals(
            plan_actions_with_mode,
            turn_number=turn_number,
            connection_id=connection_id,
        )
        if not approved_actions:
            return "Plan execution skipped: approval was not granted."

        summary = await execute_plan_actions(
            approved_actions,
            executor,
            turn_number=turn_number,
            continuation_of=continuation_of,
        )
        await self._audit("planner_actions_executed", turn_number=turn_number, summary=summary)
        return summary

    async def _ensure_plan_action_approvals(
        self,
        plan_actions: list[dict[str, object]],
        *,
        turn_number: int,
        connection_id: str,
    ) -> list[dict[str, object]]:
        """Attach approval tokens to plan actions when required and available."""
        if self._approval_flow is None:
            return plan_actions

        approved_actions: list[dict[str, object]] = []
        parser = MarkdownPlanParser()
        for index, action in enumerate(plan_actions):
            if action.get("approval_token") is not None:
                approved_actions.append(action)
                continue

            try:
                work_item = plan_action_to_work_item(
                    action,
                    parser=parser,
                    index=index,
                    turn_number=turn_number,
                )
            except ValueError:
                approved_actions.append(action)
                continue

            skill_name = extract_skill_name(action) or "plan_action"
            prepared_work_item = await resolve_work_item_approval(
                work_item,
                standing_approval_resolver=self._resolve_standing_approval_token,
                manual_approval_requester=lambda unresolved, skill_name=skill_name, connection_id=connection_id: self._approval_flow.request_skill_approval(
                    work_item=unresolved,
                    scope=ApprovalScope.full_plan,
                    skill_name=skill_name,
                    connection_id=connection_id,
                ),
            )
            if (
                prepared_work_item is None
                or prepared_work_item.approval_token is None
            ):
                await self._audit(
                    "planner_action_approval_declined",
                    turn_number=turn_number,
                    action_index=index,
                    skill_name=skill_name,
                    verdict="declined_or_missing",
                )
                continue

            if prepared_work_item.approval_token.scope == ApprovalScope.standing:
                await self._audit(
                    "planner_action_standing_approval_attached",
                    turn_number=turn_number,
                    action_index=index,
                    skill_name=skill_name,
                )
            else:
                await self._audit(
                    "planner_action_approval_attached",
                    turn_number=turn_number,
                    action_index=index,
                    skill_name=skill_name,
                )

            approved_actions.append(
                {
                    **action,
                    "approval_token": prepared_work_item.approval_token.model_dump(mode="python"),
                }
            )

        return approved_actions

    def _resolve_standing_approval_token(self, work_item: WorkItem) -> ApprovalToken | None:
        """Resolve standing approval for spawned items so manual review can be skipped."""
        approval_manager = self._turn_context().approval_manager
        if approval_manager is None:
            return None
        check_standing = getattr(approval_manager, "check_standing_approval", None)
        if not callable(check_standing):
            return None
        return check_standing(work_item, self.goal_manager)

    async def _execute_planner_skill_actions(
        self,
        plan_actions: list[dict[str, object]],
        connection_id: str,
        turn_number: int,
        fallback_response: str,
        interaction_mode: InteractionMode,
    ) -> tuple[str, dict[str, int]]:
        """Execute skill-based plan actions with approval flow."""
        payload: dict[str, int] = {
            "actions_seen": len(plan_actions),
            "skills_executed": 0, "skills_skipped": 0,
            "approval_requested": 0, "approval_approved": 0, "approval_declined": 0,
        }

        tc = self._turn_context()
        skill_registry = tc.skill_registry
        skill_executor = tc.skill_executor
        if skill_registry is None or skill_executor is None:
            return fallback_response, payload

        summary_lines: list[str] = []
        for action in plan_actions:
            line, action_payload = await self._execute_single_skill_action(
                action,
                connection_id,
                turn_number,
                skill_registry,
                skill_executor,
                interaction_mode,
            )
            if line is not None:
                summary_lines.append(line)
            for key, val in action_payload.items():
                payload[key] = payload.get(key, 0) + val

        if summary_lines:
            return "\n".join(summary_lines), payload
        return fallback_response, payload

    async def _execute_single_skill_action(
        self,
        action: dict[str, object],
        connection_id: str,
        turn_number: int,
        skill_registry: object,
        skill_executor: object,
        interaction_mode: InteractionMode,
    ) -> tuple[str | None, dict[str, int]]:
        """Execute a single skill action, returning (summary_line, counters)."""
        counters: dict[str, int] = {}
        skill_name = extract_skill_name(action)
        if not skill_name:
            return None, counters

        skill_def = skill_registry.get(skill_name)
        await self._audit("planner_skill_action_checked", turn_number=turn_number, skill_name=skill_name, skill_registered=skill_def is not None)
        if skill_def is None:
            counters["skills_skipped"] = 1
            return f"Skipped skill '{skill_name}': skill not registered.", counters

        work_item = build_skill_work_item(
            skill_name,
            action,
            turn_number,
            skill_def.requires_approval,
            interaction_mode=interaction_mode,
        )

        if skill_def.requires_approval:
            counters["approval_requested"] = 1
            await self._audit("approval_requested", turn_number=turn_number, skill_name=skill_name, scope=ApprovalScope.tool_type.value)
            decision, token = await self._approval_flow.request_skill_approval(
                work_item=work_item, scope=ApprovalScope.tool_type,
                skill_name=skill_name, connection_id=connection_id,
            )
            if decision is None or decision.verdict != ApprovalVerdict.approved or token is None:
                counters["approval_declined"] = 1
                counters["skills_skipped"] = 1
                await self._audit("skill_execution_skipped_approval", turn_number=turn_number, skill_name=skill_name,
                                  verdict=decision.verdict.value if decision is not None else "timed_out")
                return f"Skipped skill '{skill_name}': approval declined.", counters

            counters["approval_approved"] = 1
            work_item.approval_token = token

        inputs = extract_skill_inputs(action)
        skill_executor.set_work_item(work_item)
        try:
            result = await skill_executor.run_tool(skill_name, inputs)
        finally:
            skill_executor.set_work_item(None)

        if result.success:
            counters["skills_executed"] = 1
            line = f"Executed skill '{skill_name}'."
        else:
            counters["skills_skipped"] = 1
            line = f"Failed skill '{skill_name}': {result.error or 'execution failed'}."

        await self._audit("planner_skill_action_executed", turn_number=turn_number, skill_name=skill_name, success=result.success, error=result.error)
        return line, counters

    # ── Output Gates ───────────────────────────────────────────────────

    async def _evaluate_output_gates(
        self, response_text: str, response_taint: TaintLevel, sender_id: str, turn_number: int,
    ) -> tuple[str, list[str]]:
        blocked_gate_names: list[str] = []
        if self.output_gate_runner is None:
            await self._audit("output_gates_evaluated", turn_number=turn_number, results=[], configured=False)
            return response_text, blocked_gate_names

        response_text, gate_results = self.output_gate_runner.evaluate_output(
            response_text=response_text, response_taint=response_taint, sender_id=sender_id,
        )
        results_payload = [r.model_dump(mode="json") for r in gate_results]
        warnings = [r.model_dump(mode="json") for r in gate_results if "warn" in r.flags]
        blocked_gate_names = [r.gate_name for r in gate_results if r.action == "block"]

        await self._audit("output_gates_evaluated", turn_number=turn_number, results=results_payload, configured=True)
        if warnings:
            await self._audit("output_gate_warnings", turn_number=turn_number, warnings=warnings)
        if blocked_gate_names:
            response_text = "I cannot share that"
            await self._audit("output_gate_blocked", turn_number=turn_number, blocked_gates=blocked_gate_names)
        return response_text, blocked_gate_names

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

    # ── Memory ─────────────────────────────────────────────────────────

    async def _auto_retrieve_memories(
        self, text: str, cm: LiveContextManager | None, taint: TaintLevel, turn_number: int,
    ) -> None:
        tc = self._turn_context()
        memory_store = tc.memory_store
        if memory_store is None or cm is None:
            return

        recalled_keyword = await memory_store.search_keyword(text, limit=3)
        recalled_entity: list[MemoryItem] = []
        mentions = self._extract_mentions(text)
        if mentions:
            entity_candidates = await memory_store.search_by_type(MemoryType.entity, limit=50)
            recalled_entity = [item for item in entity_candidates if self._memory_matches_any_mention(item, mentions)]

        recalled_unique: dict[str, MemoryItem] = {}
        for item in [*recalled_keyword, *recalled_entity]:
            recalled_unique.setdefault(item.memory_id, item)

        for item in recalled_unique.values():
            await memory_store.increment_access(item.memory_id)
            cm.add(
                tc.scope_id,
                ContextItem(
                    ctx_id=f"memory:{item.memory_id}",
                    zone=ContextZone.memory,
                    content=item.content,
                    token_count=_counter.count(item.content),
                    created_at=datetime.now(UTC),
                    turn_number=turn_number,
                    source="memory:auto_retrieve",
                    taint=item.taint,
                    kind="memory",
                ),
            )

    async def _ingest_raw_memory(self, text: str, taint: TaintLevel, session_id: str, turn_number: int) -> None:
        tc = self._turn_context()
        memory_store = tc.memory_store
        if memory_store is None:
            return
        await memory_store.store_raw(
            MemoryItem(
                memory_id=f"raw:{tc.scope_id}:{turn_number}:{uuid.uuid4().hex}",
                content=text,
                memory_type=MemoryType.episode,
                reingestion_tier=ReingestionTier.low_reingestion,
                taint=taint,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                session_id=session_id,
                source_kind="conversation_raw",
            ),
        )

    async def _process_memory_queries(
        self,
        response: AgentResponse | None,
        request_taint: TaintLevel,
        session_id: str,
        scope_id: str,
        cm: LiveContextManager | None,
        turn_number: int,
    ) -> list[MemoryItem]:
        """Step 9: run memory queries the agent attached to its response.

        Taint gate: external-tainted contexts must not receive owner-tainted
        memories, preventing data leakage across trust boundaries.
        """
        if response is None or not response.memory_queries:
            await self._audit("memory_queries_skipped", step=9, reason="no queries")
            return []

        tc = self._turn_context()
        memory_store = tc.memory_store
        if memory_store is None:
            await self._audit("memory_queries_skipped", step=9, reason="no memory store")
            return []

        retriever = SilasMemoryRetriever(memory_store)
        all_results: list[MemoryItem] = []

        for query in response.memory_queries:
            results = await retriever.retrieve(query, scope_id=scope_id, session_id=session_id)

            # Taint gate: strip owner memories when the request came from
            # an external context — prevents cross-boundary data leakage.
            if request_taint == TaintLevel.external:
                results = [r for r in results if r.taint != TaintLevel.owner]

            all_results.extend(results)
            await self._audit(
                "memory_query_executed",
                step=9,
                strategy=query.strategy.value,
                query=query.query,
                result_count=len(results),
            )

        # Inject retrieved memories into live context for the next turn.
        if all_results and cm is not None:
            for item in all_results:
                cm.add(scope_id, ContextItem(
                    ctx_id=f"mem_recall:{turn_number}:{item.memory_id}",
                    zone=ContextZone.memory,
                    content=item.content,
                    token_count=_counter.count(item.content),
                    created_at=datetime.now(UTC),
                    turn_number=turn_number,
                    source="memory:query_result",
                    taint=item.taint,
                    kind="memory",
                ))

        return all_results

    async def _process_memory_ops(
        self,
        response: AgentResponse | None,
        request_taint: TaintLevel,
        session_id: str,
        turn_number: int,
    ) -> None:
        """Step 10: execute memory write ops the agent requested.

        All ops are gated: external-tainted requests cannot write memories
        (prevents prompt-injection from persisting attacker content).
        Store/update/delete each route to the appropriate MemoryStore method.
        """
        if response is None or not response.memory_ops:
            await self._audit("memory_ops_skipped", step=10, reason="no ops")
            return

        tc = self._turn_context()
        memory_store = tc.memory_store
        if memory_store is None:
            await self._audit("memory_ops_skipped", step=10, reason="no memory store")
            return

        # Truncate excess memory ops per spec max_memory_ops_per_turn.
        _stream_cfg = getattr(tc.config, "stream", None) if tc.config is not None else None
        max_ops: int = getattr(_stream_cfg, "max_memory_ops_per_turn", 10)
        ops = response.memory_ops
        if len(ops) > max_ops:
            dropped = len(ops) - max_ops
            logger.warning(
                "Truncating memory ops from %d to %d (dropped %d)",
                len(ops),
                max_ops,
                dropped,
            )
            await self._audit(
                "memory_ops_truncated",
                step=10,
                requested=len(ops),
                allowed=max_ops,
                dropped=dropped,
            )
            ops = ops[:max_ops]

        # Hard gate: external contexts cannot write memories at all.
        if request_taint == TaintLevel.external:
            await self._audit(
                "memory_ops_blocked",
                step=10,
                reason="external taint",
                op_count=len(ops),
            )
            return

        for op in ops:
            try:
                await self._execute_single_memory_op(memory_store, op, session_id, turn_number)
                await self._audit(
                    "memory_op_executed",
                    step=10,
                    op=op.op.value,
                    memory_id=op.memory_id,
                )
            except Exception as exc:
                await self._audit(
                    "memory_op_failed",
                    step=10,
                    op=op.op.value,
                    memory_id=op.memory_id,
                    error=str(exc),
                )

    async def _execute_single_memory_op(
        self,
        memory_store: MemoryStore,
        op: MemoryOp,
        session_id: str,
        turn_number: int,
    ) -> None:
        """Dispatch a single memory op to the store."""
        if op.op == MemoryOpType.store:
            tc = self._turn_context()
            await memory_store.store(
                MemoryItem(
                    memory_id=f"agent_op:{tc.scope_id}:{turn_number}:{uuid.uuid4().hex}",
                    content=op.content or "",
                    memory_type=op.memory_type,
                    taint=TaintLevel.owner,
                    semantic_tags=op.tags,
                    entity_refs=op.entity_refs,
                    session_id=session_id,
                    source_kind="agent_memory_op",
                ),
            )
        elif op.op == MemoryOpType.update:
            assert op.memory_id is not None  # validated by MemoryOp
            await memory_store.update(op.memory_id, content=op.content)
        elif op.op == MemoryOpType.delete:
            assert op.memory_id is not None
            await memory_store.delete(op.memory_id)
        elif op.op == MemoryOpType.link:
            # Link ops update causal_refs — the lightweight graph edge.
            assert op.memory_id is not None
            assert op.link_to is not None
            existing = await memory_store.get(op.memory_id)
            if existing is not None:
                new_refs = [*existing.causal_refs, op.link_to]
                await memory_store.update(op.memory_id, causal_refs=new_refs)

    def _take_evicted_context_items(
        self,
        context_manager: LiveContextManager | None,
        scope_id: str,
    ) -> list[ContextItem]:
        if context_manager is None:
            return []
        take_last_evicted = getattr(context_manager, "take_last_evicted", None)
        if not callable(take_last_evicted):
            return []
        raw_items = take_last_evicted(scope_id)
        if not isinstance(raw_items, list):
            return []
        return [item for item in raw_items if isinstance(item, ContextItem)]

    async def _persist_evicted_context(
        self,
        evicted_items: list[ContextItem],
        session_id: str,
        turn_number: int,
    ) -> None:
        tc = self._turn_context()
        memory_store = tc.memory_store
        if memory_store is None or not evicted_items:
            return

        for item in evicted_items:
            await memory_store.store(
                MemoryItem(
                    memory_id=f"evicted:{tc.scope_id}:{turn_number}:{uuid.uuid4().hex}",
                    content=item.content,
                    memory_type=MemoryType.episode,
                    reingestion_tier=ReingestionTier.low_reingestion,
                    taint=item.taint,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    session_id=session_id,
                    source_kind="context_eviction",
                )
            )

    # ── Helpers ────────────────────────────────────────────────────────

    async def _ensure_persona_state_loaded(self, scope_id: str) -> None:
        if scope_id not in self._pending_persona_scopes:
            return

        personality_engine = self._turn_context().personality_engine
        if personality_engine is None:
            self._pending_persona_scopes.discard(scope_id)
            await self._audit("stream_startup_dependency_missing", dependency="personality_engine")
            return

        try:
            await personality_engine.get_effective_axes(scope_id, "default")
        except (RuntimeError, ValueError, OSError) as exc:
            await self._audit("persona_state_lazy_load_failed", scope_id=scope_id, error=str(exc))
        finally:
            self._pending_persona_scopes.discard(scope_id)

    def _known_scopes(self) -> list[str]:
        scopes = {self._turn_context().scope_id}
        scopes.update(
            processor.turn_context.scope_id
            for processor in self._turn_processors.values()
        )

        context_manager = self._get_context_manager()
        if context_manager is not None:
            by_scope = getattr(context_manager, "by_scope", None)
            if isinstance(by_scope, dict):
                scopes.update(
                    scope_id for scope_id in by_scope
                    if isinstance(scope_id, str) and scope_id.strip()
                )

        chronicle_store = self._turn_context().chronicle_store
        if chronicle_store is not None:
            for attr_name in ("by_scope", "known_scopes", "scopes"):
                raw = getattr(chronicle_store, attr_name, None)
                if isinstance(raw, (dict, list, tuple, set)):
                    scopes.update(
                        scope_id for scope_id in raw
                        if isinstance(scope_id, str) and scope_id.strip()
                    )

        return sorted(scopes)

    def _config_value(self, *path: str, default: object | None = None) -> object | None:
        current: object | None = self._turn_context().config
        for key in path:
            if current is None:
                return default
            if isinstance(current, dict):
                current = current.get(key)
                continue
            current = getattr(current, key, None)
        if current is None:
            return default
        return current

    def _rehydration_max_chronicle_entries(self) -> int:
        value = self._config_value("rehydration", "max_chronicle_entries", default=50)
        if not isinstance(value, int) or value < 1:
            return 50
        return value

    def _observation_mask_after_turns(self) -> int:
        value = self._config_value("context", "observation_mask_after_turns", default=5)
        if not isinstance(value, int) or value < 0:
            return 5
        return value

    def _masked_if_stale(
        self,
        item: ContextItem,
        latest_turn: int,
        mask_after_turns: int,
    ) -> ContextItem:
        if item.kind != "tool_result" or item.masked:
            return item
        if latest_turn - item.turn_number <= mask_after_turns:
            return item

        placeholder = (
            f"[Result of {item.source} — {item.token_count} tokens — see memory for details]"
        )
        return item.model_copy(
            update={
                "content": placeholder,
                "token_count": _counter.count(placeholder),
                "masked": True,
            }
        )

    def _replace_context_item(
        self,
        cm: LiveContextManager,
        scope_id: str,
        item: ContextItem,
    ) -> None:
        cm.drop(scope_id, item.ctx_id)
        cm.add(scope_id, item)

    def _constitution_content(self) -> str:
        raw_constitution = self._config_value("personality", "constitution")
        if isinstance(raw_constitution, list):
            lines = [f"- {line}" for line in raw_constitution if isinstance(line, str) and line.strip()]
            if lines:
                return "Constitution:\n" + "\n".join(lines)
        return (
            "Constitution:\n"
            "- Never fabricate information.\n"
            "- Keep private data private.\n"
            "- Require approval for state-changing actions."
        )

    def _tool_descriptions_content(self) -> str:
        skill_registry = self._turn_context().skill_registry
        if skill_registry is None:
            return "Tool descriptions: no registered skills."

        descriptions = [
            f"- {skill.name}: {skill.description}"
            for skill in skill_registry.list_all()
            if skill.description.strip()
        ]
        if not descriptions:
            return "Tool descriptions: no registered skills."
        return "Tool descriptions:\n" + "\n".join(descriptions)

    def _configuration_content(self) -> str:
        config = self._turn_context().config
        if config is None:
            return "Runtime configuration snapshot: <missing>"

        payload: object
        model_dump = getattr(config, "model_dump", None)
        if callable(model_dump):
            payload = model_dump(mode="json")
        elif isinstance(config, dict):
            payload = config
        else:
            payload = getattr(config, "__dict__", str(config))

        serialized = json.dumps(payload, sort_keys=True, default=str)
        return f"Runtime configuration snapshot:\n{serialized}"

    def _file_subscription_targets(self, item: WorkItem) -> tuple[str, ...]:
        targets: list[str] = []
        for raw_target in item.input_artifacts_from:
            if not isinstance(raw_target, str):
                continue
            target = raw_target.strip()
            if not target or target in targets:
                continue
            targets.append(target)
        return tuple(targets)

    def _has_active_goal_connection_dependencies(self) -> bool:
        active_goal = self._config_value("active_goal")
        if not isinstance(active_goal, str) or not active_goal.strip():
            return False

        raw_dependencies = self._config_value("active_goal_connection_dependencies")
        if isinstance(raw_dependencies, bool):
            return raw_dependencies
        if isinstance(raw_dependencies, (list, tuple, set)):
            return bool(raw_dependencies)
        return True

    @staticmethod
    def _is_cron_schedule(schedule: str | None) -> bool:
        if not isinstance(schedule, str):
            return False
        parts = schedule.split()
        return len(parts) == 5

    def _prepend_high_confidence_suggestions(self, response_text: str, suggestions: list[SuggestionProposal]) -> str:
        if not suggestions:
            return response_text
        preface = "\n".join(f"Suggestion: {suggestion.text}" for suggestion in suggestions)
        return f"{preface}\n\n{response_text}" if response_text else preface

    async def _prepare_agent_toolsets(
        self,
        *,
        connection_id: str,
        turn_number: int,
    ) -> tuple[ApprovalRequiredToolset | None, ApprovalRequiredToolset | None]:
        tc = self._turn_context()
        resolver = tc.skill_resolver
        if resolver is None:
            await self._audit(
                "skill_toolsets_prepared",
                step=6.5,
                connection_id=connection_id,
                prepared=False,
                reason="skill_resolver_missing",
            )
            return None, None

        active_work_item = await self._find_active_toolset_work_item()
        work_item_for_tools = active_work_item or self._build_synthetic_toolset_work_item(turn_number)
        active_work_item_id = active_work_item.id if active_work_item is not None else None

        proxy_toolset = self._build_role_toolset(
            resolver=resolver,
            work_item=work_item_for_tools,
            agent_role="proxy",
        )
        planner_toolset = self._build_role_toolset(
            resolver=resolver,
            work_item=work_item_for_tools,
            agent_role="planner",
        )
        await self._audit(
            "skill_toolsets_prepared",
            step=6.5,
            connection_id=connection_id,
            prepared=True,
            work_item_id=active_work_item_id,
            proxy_tools=self._tool_names(proxy_toolset),
            planner_tools=self._tool_names(planner_toolset),
        )
        return proxy_toolset, planner_toolset

    async def _find_active_toolset_work_item(self) -> WorkItem | None:
        store = self.work_item_store
        if store is None:
            return None

        for status in _IN_PROGRESS_STATUSES:
            try:
                items = await store.list_by_status(status)
            except (OSError, RuntimeError, ValueError):
                return None
            if not items:
                continue
            ordered = sorted(items, key=lambda item: (item.created_at, item.id))
            return ordered[0].model_copy(deep=True)

        return None

    def _build_role_toolset(
        self,
        *,
        resolver: SkillResolver,
        work_item: WorkItem,
        agent_role: str,
    ) -> ApprovalRequiredToolset:
        base_tools = self._base_tools_for_role(agent_role)
        allowed_tools = sorted({
            *[tool.name for tool in base_tools],
            *self._available_skill_names(),
            *work_item.skills,
        })

        try:
            prepared = resolver.prepare_toolset(
                work_item=work_item,
                agent_role=agent_role,
                base_toolset=base_tools,
                allowed_tools=allowed_tools,
            )
            if isinstance(prepared, ApprovalRequiredToolset):
                return prepared
        except (OSError, RuntimeError, TypeError, ValueError):
            pass

        return ApprovalRequiredToolset(
            inner=FilteredToolset(
                inner=PreparedToolset(
                    inner=SkillToolset(base_toolset=base_tools, skill_metadata=[]),
                    agent_role=agent_role,
                ),
                allowed_tools=allowed_tools,
            )
        )

    def _base_tools_for_role(self, agent_role: str) -> list[ToolDefinition]:
        catalog = _PLANNER_BASE_TOOLS if agent_role == "planner" else _PROXY_BASE_TOOLS
        return [
            ToolDefinition(
                name=name,
                description=description,
                input_schema={"type": "object"},
            )
            for name, description in catalog
        ]

    def _build_synthetic_toolset_work_item(self, turn_number: int) -> WorkItem:
        scope_id = self._turn_context().scope_id
        return WorkItem(
            id=f"toolset:{scope_id}:{turn_number}",
            type=WorkItemType.task,
            title="Turn-scoped toolset preparation",
            body="No active work item available for this turn.",
            skills=[],
            needs_approval=False,
        )

    def _tool_names(self, toolset: ApprovalRequiredToolset | None) -> list[str]:
        if toolset is None:
            return []
        return [tool.name for tool in toolset.list_tools()]

    def _render_toolset_manifest(self, toolset: ApprovalRequiredToolset | None) -> str:
        if toolset is None:
            return ""
        tools = toolset.list_tools()
        if not tools:
            return ""

        lines: list[str] = []
        for tool in tools:
            description = " ".join(tool.description.split())
            approval_note = " [approval required]" if tool.requires_approval else ""
            lines.append(f"- {tool.name}{approval_note}: {description}")
        return "\n".join(lines)

    def _build_proxy_prompt(
        self,
        message_text: str,
        rendered_context: str,
        *,
        toolset: ApprovalRequiredToolset | None = None,
    ) -> str:
        sections: list[str] = []
        if rendered_context.strip():
            sections.append(f"[CONTEXT]\n{rendered_context}")
        tool_manifest = self._render_toolset_manifest(toolset)
        if tool_manifest:
            sections.append(f"[AVAILABLE TOOLS]\n{tool_manifest}")
        sections.append(f"[USER MESSAGE]\n{message_text}")
        return "\n\n".join(sections)

    def _build_planner_prompt(
        self,
        message_text: str,
        rendered_context: str,
        *,
        toolset: ApprovalRequiredToolset | None = None,
    ) -> str:
        sections: list[str] = []
        if rendered_context.strip():
            sections.append(f"[CONTEXT]\n{rendered_context}")
        tool_manifest = self._render_toolset_manifest(toolset)
        if tool_manifest:
            sections.append(f"[AVAILABLE TOOLS]\n{tool_manifest}")
        sections.append(f"[USER REQUEST]\n{message_text}")
        return "\n\n".join(sections)

    def _route_response_text(self, routed: RouteDecision) -> str:
        if routed.route == "planner":
            return "I need to plan this request before execution. Planner execution is not available yet."
        return routed.response.message if routed.response is not None else ""

    def _available_skill_names(self) -> list[str]:
        registry = self._turn_context().skill_registry
        if registry is None:
            return []
        return [skill.name for skill in registry.list_all()]

    def _extract_plan_actions(self, routed: RouteDecision) -> list[dict[str, object]]:
        raw_actions = getattr(routed, "plan_actions", None)
        if not isinstance(raw_actions, list):
            return []
        normalized: list[dict[str, object]] = []
        for action in raw_actions:
            if isinstance(action, PlanAction):
                normalized.append(action.model_dump(mode="json"))
            elif isinstance(action, dict):
                normalized.append(action)
        return normalized

    async def _audit(self, event: str, **data: object) -> None:
        audit_log = self._turn_context().audit
        if audit_log is None:
            return
        await audit_log.log(event, **data)

    def _should_use_queue_path(self) -> bool:
        """Determine whether this turn should use queue-based execution.

        Why health check here: even if queue_bridge is set, consumers may
        have crashed. Falling back to procedural prevents silent failures.
        """
        if self.queue_bridge is None:
            return False

        # Config kill-switch: allows disabling queue path without removing bridge.
        use_queue = self._config_value("execution", "use_queue_path", default=True)
        if use_queue is False or use_queue == 0:
            return False

        orchestrator = self.queue_bridge.orchestrator
        if not orchestrator.running:
            import logging
            logging.getLogger(__name__).warning(
                "Queue orchestrator not running — falling back to procedural path"
            )
            return False

        return True

    async def _start_queue_orchestrator(self) -> None:
        """Start queue consumers if a bridge is configured."""
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
        """Stop queue consumers during shutdown."""
        if self.queue_bridge is None:
            return
        try:
            await self.queue_bridge.orchestrator.stop()
            await self._audit("queue_orchestrator_stopped")
        except Exception as exc:
            await self._audit(
                "queue_orchestrator_stop_failed", error=str(exc),
            )

    def _get_context_manager(self) -> LiveContextManager | None:
        if self.context_manager is not None:
            return self.context_manager
        return self._turn_context().context_manager

    def _get_suggestion_engine(self) -> SuggestionEngine | None:
        if self.suggestion_engine is not None:
            return self.suggestion_engine
        return self._turn_context().suggestion_engine

    def _get_autonomy_calibrator(self) -> AutonomyCalibrator | None:
        if self.autonomy_calibrator is not None:
            return self.autonomy_calibrator
        return self._turn_context().autonomy_calibrator

    def _ensure_session_id(self) -> str:
        active_session_id = self._active_session_id.get()
        if active_session_id is not None:
            return active_session_id
        if self.session_id is None:
            self.session_id = str(uuid.uuid4())
        return self.session_id

    async def _on_approval_response(self, token_id: str, verdict: ApprovalVerdict, resolved_by: str) -> None:
        resolved = await self._approval_flow.handle_response(token_id, verdict, resolved_by)
        if resolved:
            await self._audit("approval_resolved", token_id=token_id, verdict=verdict.value, resolved_by=resolved_by)
        else:
            await self._audit("approval_response_ignored", token_id=token_id, verdict=verdict.value, resolved_by=resolved_by)

    @staticmethod
    def _extract_mentions(message_text: str) -> set[str]:
        return {match.lstrip("@").lower() for match in _MENTION_PATTERN.findall(message_text)}

    @staticmethod
    def _memory_matches_any_mention(item: MemoryItem, mentions: set[str]) -> bool:
        if not mentions:
            return False
        content_lower = item.content.lower()
        memory_id_lower = item.memory_id.lower()
        entity_refs_lower = {ref.lstrip("@").lower() for ref in item.entity_refs}
        semantic_tags_lower = [tag.lstrip("@").lower() for tag in item.semantic_tags]
        return any(
            mention in content_lower
            or mention in memory_id_lower
            or mention in entity_refs_lower
            or any(mention in tag for tag in semantic_tags_lower)
            for mention in mentions
        )


__all__ = ["Stream"]
