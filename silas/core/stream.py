"""The Stream — Silas's permanent orchestration session.

Core turn-processing loop. Plan execution and approval flow are
delegated to silas.core.plan_executor and silas.core.approval_flow
to keep this file focused on orchestration.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from silas.agents.structured import run_structured_agent
from silas.core.approval_flow import ApprovalFlow
from silas.core.context_manager import LiveContextManager
from silas.core.logging import correlation_scope
from silas.core.plan_executor import (
    build_skill_work_item,
    execute_plan_actions,
    extract_skill_inputs,
    extract_skill_name,
)
from silas.core.token_counter import HeuristicTokenCounter
from silas.core.turn_context import TurnContext
from silas.gates import OutputGateRunner
from silas.models.agents import AgentResponse, PlanAction, RouteDecision
from silas.models.approval import ApprovalScope, ApprovalVerdict
from silas.models.context import ContextItem, ContextZone
from silas.models.gates import Gate, GateResult, GateTrigger
from silas.models.memory import MemoryItem, MemoryType, ReingestionTier
from silas.models.messages import (
    ChannelMessage,
    SignedMessage,
    TaintLevel,
    signed_message_canonical_bytes,
)
from silas.models.proactivity import SuggestionProposal
from silas.models.work import WorkItem, WorkItemType
from silas.protocols.approval import NonceStore
from silas.protocols.channels import ChannelAdapterCore
from silas.protocols.proactivity import AutonomyCalibrator, SuggestionEngine

_counter = HeuristicTokenCounter()
_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_:-]+)")


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
    _approval_flow: ApprovalFlow | None = None
    # Startup should inject an Ed25519 key; bytes remains for legacy HMAC-only tests.
    _signing_key: Ed25519PrivateKey | bytes | None = None
    _nonce_store: NonceStore | None = None

    def __post_init__(self) -> None:
        if self._signing_key is None:
            self._signing_key = uuid.uuid4().bytes + uuid.uuid4().bytes
        if self._nonce_store is None:
            self._nonce_store = _InMemoryNonceStore()
        self._sync_turn_context_fields()
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

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        self._ensure_session_id()
        await self._rehydrate()
        async for message, connection_id in self.channel.listen():
            await self._process_turn(message, connection_id)

    async def _rehydrate(self) -> None:
        """Restore state from previous run (spec §5.1.3)."""
        tc = self.turn_context
        cm = self._get_context_manager()
        session_id = self._ensure_session_id()

        if tc.chronicle_store is not None and cm is not None:
            recent = await tc.chronicle_store.get_recent(tc.scope_id, limit=50)
            for item in recent:
                cm.add(tc.scope_id, item)
            if recent:
                tc.turn_number = max(item.turn_number for item in recent)

        if tc.memory_store is not None and cm is not None:
            await self._rehydrate_memories(tc, cm, session_id)

        await self._audit("stream_rehydrated", turn_number=tc.turn_number)

    async def _rehydrate_memories(
        self, tc: TurnContext, cm: LiveContextManager, session_id: str,
    ) -> None:
        """Load profile and recent session memories into context."""
        profile_items = await tc.memory_store.search_keyword("user profile preferences", limit=1)
        for item in profile_items:
            cm.add(
                tc.scope_id,
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

        recent_session = await tc.memory_store.search_session(session_id)
        for item in recent_session[:10]:
            cm.add(
                tc.scope_id,
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
        session_id = self._ensure_session_id()
        scope_id = self.turn_context.scope_id
        self.turn_context.turn_number += 1
        turn_number = self.turn_context.turn_number
        turn_id = f"{scope_id}:{turn_number}"

        with correlation_scope(turn_id=turn_id, scope_id=scope_id):
            active_gates = self._precompile_active_gates()
            await self._audit(
                "active_gates_precompiled",
                step=0,
                turn_number=turn_number,
                active_gate_count=len(active_gates),
            )
            high_confidence_suggestions = await self._collect_suggestions(connection_id)
            processed_message_text, blocked_response = await self._run_input_gates(
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
            if self.turn_context.chronicle_store is not None:
                await self.turn_context.chronicle_store.append(scope_id, chronicle_item)

            await self._auto_retrieve_memories(signed.message.text, cm, signed.taint, turn_number)
            await self._ingest_raw_memory(signed.message.text, signed.taint, session_id, turn_number)

            evicted_ctx_ids: list[str] = []
            if cm is not None:
                evicted_ctx_ids = cm.enforce_budget(
                    scope_id,
                    turn_number=turn_number,
                    current_goal=None,
                )
            if evicted_ctx_ids:
                evicted_items = self._take_evicted_context_items(cm, scope_id)
                await self._persist_evicted_context(evicted_items, session_id, turn_number)
                await self._audit(
                    "context_budget_enforced",
                    step=5,
                    turn_number=turn_number,
                    evicted_ctx_ids=evicted_ctx_ids,
                )
            available_skills = self._available_skill_names()
            await self._audit(
                "skill_availability_checked",
                step=6,
                available_skills=available_skills,
                has_skills=bool(available_skills),
            )
            await self._audit(
                "phase1a_noop", step=6.5, note="skill-aware toolset preparation deferred",
            )

            if self.turn_context.proxy is None:
                raise RuntimeError("turn_context.proxy is required")

            rendered_context = ""
            if cm is not None:
                rendered_context = cm.render(scope_id, turn_number)

            routed = await run_structured_agent(
                agent=self.turn_context.proxy,
                prompt=self._build_proxy_prompt(processed_message_text, rendered_context),
                call_name="proxy",
                default_context_profile=self.default_context_profile,
            )
            if not isinstance(routed, RouteDecision):
                raise TypeError("proxy must return RouteDecision")

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
            )
            response_text = self._prepend_high_confidence_suggestions(
                response_text, high_confidence_suggestions,
            )
            response_text, blocked_gate_names = await self._evaluate_output_gates(
                response_text, signed.taint, message.sender_id, turn_number,
            )

            await self._audit("phase1a_noop", step=9, note="memory query processing skipped")
            await self._audit("phase1a_noop", step=10, note="memory op processing skipped")

            response_item = ContextItem(
                ctx_id=f"chronicle:{turn_number}:resp:{uuid.uuid4().hex}",
                zone=ContextZone.chronicle,
                content=f"Silas: {response_text}",
                token_count=_counter.count(response_text),
                created_at=datetime.now(UTC),
                turn_number=turn_number,
                source="agent:proxy",
                taint=TaintLevel.owner,
                kind="message",
            )
            if cm is not None:
                cm.add(scope_id, response_item)
            if self.turn_context.chronicle_store is not None:
                await self.turn_context.chronicle_store.append(scope_id, response_item)

            await self._audit("phase1a_noop", step=11.5, note="raw output ingest skipped")
            await self.channel.send(connection_id, response_text, reply_to=message.reply_to)
            await self._audit("phase1a_noop", step=14, note="access state updates skipped")
            await self._record_autonomy_outcome(
                turn_number=turn_number, route=routed.route, blocked=bool(blocked_gate_names),
            )
            await self._audit("turn_processed", turn_number=turn_number, route=routed.route)

            return response_text

    async def verify_inbound(self, signed_message: SignedMessage) -> tuple[bool, str]:
        """Validate inbound message trust before owner-level actions rely on it.

        Why: the stream's taint boundary depends on cryptographic provenance and
        single-use nonces so replayed or forged payloads cannot escalate trust.
        """
        payload = signed_message_canonical_bytes(signed_message.message, signed_message.nonce)
        if not self._is_valid_signature(payload, signed_message.signature):
            return False, "invalid_signature"

        nonce_store = self._nonce_store
        if nonce_store is None:
            return False, "nonce_store_unavailable"

        if await nonce_store.is_used("msg", signed_message.nonce):
            return False, "nonce_replay"
        await nonce_store.record("msg", signed_message.nonce)
        return True, "ok"

    async def _prepare_signed_inbound_message(
        self,
        *,
        message: ChannelMessage,
        processed_message_text: str,
        turn_number: int,
    ) -> SignedMessage:
        signed = self._sign_inbound_message(message, processed_message_text)
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

    def _sign_inbound_message(
        self,
        message: ChannelMessage,
        processed_message_text: str,
    ) -> SignedMessage:
        nonce = uuid.uuid4().hex
        message_payload = message.model_copy(update={"text": processed_message_text})
        canonical_payload = signed_message_canonical_bytes(message_payload, nonce)
        signature = self._sign_payload(canonical_payload)
        return SignedMessage(
            message=message_payload,
            signature=signature,
            nonce=nonce,
            taint=TaintLevel.external,
        )

    def _sign_payload(self, canonical_payload: bytes) -> bytes:
        signing_key = self._signing_key
        if isinstance(signing_key, Ed25519PrivateKey):
            return signing_key.sign(canonical_payload)
        if isinstance(signing_key, bytes):
            return hmac.new(signing_key, canonical_payload, hashlib.sha256).digest()
        raise RuntimeError("stream signing key is not configured")

    def _is_valid_signature(self, canonical_payload: bytes, signature: bytes) -> bool:
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
        if is_verified and sender_id == self.owner_id:
            return TaintLevel.owner
        return TaintLevel.external

    # ── Planner Route Handling ─────────────────────────────────────────

    async def _handle_planner_route(
        self,
        routed: RouteDecision,
        response_text: str,
        connection_id: str,
        turn_number: int,
        message_text: str,
        rendered_context: str,
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
                )
                if work_exec_summary is not None:
                    response_text = work_exec_summary
                else:
                    response_text, plan_flow_payload = await self._execute_planner_skill_actions(
                        plan_actions=plan_actions, connection_id=connection_id,
                        turn_number=turn_number, fallback_response=response_text,
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
        gate_runner = self.turn_context.gate_runner
        if gate_runner is None:
            return tuple(system_gates)

        precompile = getattr(gate_runner, "precompile_turn_gates", None)
        if not callable(precompile):
            return tuple(system_gates)

        compiled = precompile(system_gates=system_gates)
        return tuple(compiled)

    def _load_system_gates(self) -> list[Gate]:
        config = self.turn_context.config
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
    ) -> tuple[str, str | None]:
        gate_runner = self.turn_context.gate_runner
        if gate_runner is None or not active_gates:
            await self._audit(
                "input_gates_evaluated",
                step=1,
                turn_number=turn_number,
                policy_results=[],
                quality_results=[],
                configured=bool(active_gates),
            )
            return message.text, None

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
                return message.text, blocked

            if result.action == "block":
                await self._audit(
                    "input_gate_blocked",
                    turn_number=turn_number,
                    gate_name=result.gate_name,
                    reason=result.reason,
                )
                blocked = self._input_gate_block_response(merged_context, result)
                return message.text, blocked

        rewritten = merged_context.get("message")
        if isinstance(rewritten, str) and rewritten.strip():
            return rewritten, None
        return message.text, None

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
    ) -> tuple[list[dict[str, object]], str | None]:
        planner = self.turn_context.planner
        if planner is None:
            await self._audit("planner_handoff_missing", turn_number=turn_number)
            return [], None

        planner_output = await run_structured_agent(
            agent=planner,
            prompt=self._build_planner_prompt(message_text, rendered_context),
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
        self, plan_actions: list[dict[str, object]], turn_number: int, continuation_of: str | None,
    ) -> str | None:
        """Try executing plan actions via work executor. Returns summary or None."""
        executor = self.turn_context.work_executor
        if executor is None:
            return None

        summary = await execute_plan_actions(
            plan_actions, executor, turn_number=turn_number, continuation_of=continuation_of,
        )
        await self._audit("planner_actions_executed", turn_number=turn_number, summary=summary)
        return summary

    async def _execute_planner_skill_actions(
        self,
        plan_actions: list[dict[str, object]],
        connection_id: str,
        turn_number: int,
        fallback_response: str,
    ) -> tuple[str, dict[str, int]]:
        """Execute skill-based plan actions with approval flow."""
        payload: dict[str, int] = {
            "actions_seen": len(plan_actions),
            "skills_executed": 0, "skills_skipped": 0,
            "approval_requested": 0, "approval_approved": 0, "approval_declined": 0,
        }

        skill_registry = self.turn_context.skill_registry
        skill_executor = self.turn_context.skill_executor
        if skill_registry is None or skill_executor is None:
            return fallback_response, payload

        summary_lines: list[str] = []
        for action in plan_actions:
            line, action_payload = await self._execute_single_skill_action(
                action, connection_id, turn_number, skill_registry, skill_executor,
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

        work_item = build_skill_work_item(skill_name, action, turn_number, skill_def.requires_approval)

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
            result = await skill_executor.execute(skill_name, inputs)
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

        response_text, gate_results = self.output_gate_runner.evaluate(
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

        idle = await engine.generate_idle(self.turn_context.scope_id, datetime.now(UTC))
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
        await calibrator.record_outcome(self.turn_context.scope_id, action_family=route, outcome=outcome)
        await self._audit("autonomy_calibration_recorded", step=15, turn_number=turn_number, action_family=route, outcome=outcome)

    # ── Memory ─────────────────────────────────────────────────────────

    async def _auto_retrieve_memories(
        self, text: str, cm: LiveContextManager | None, taint: TaintLevel, turn_number: int,
    ) -> None:
        if self.turn_context.memory_store is None or cm is None:
            return

        recalled_keyword = await self.turn_context.memory_store.search_keyword(text, limit=3)
        recalled_entity: list[MemoryItem] = []
        mentions = self._extract_mentions(text)
        if mentions:
            entity_candidates = await self.turn_context.memory_store.search_by_type(MemoryType.entity, limit=50)
            recalled_entity = [item for item in entity_candidates if self._memory_matches_any_mention(item, mentions)]

        recalled_unique: dict[str, MemoryItem] = {}
        for item in [*recalled_keyword, *recalled_entity]:
            recalled_unique.setdefault(item.memory_id, item)

        for item in recalled_unique.values():
            await self.turn_context.memory_store.increment_access(item.memory_id)
            cm.add(
                self.turn_context.scope_id,
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
        if self.turn_context.memory_store is None:
            return
        await self.turn_context.memory_store.store_raw(
            MemoryItem(
                memory_id=f"raw:{self.turn_context.scope_id}:{turn_number}:{uuid.uuid4().hex}",
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
        memory_store = self.turn_context.memory_store
        if memory_store is None or not evicted_items:
            return

        for item in evicted_items:
            await memory_store.store(
                MemoryItem(
                    memory_id=f"evicted:{self.turn_context.scope_id}:{turn_number}:{uuid.uuid4().hex}",
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

    def _prepend_high_confidence_suggestions(self, response_text: str, suggestions: list[SuggestionProposal]) -> str:
        if not suggestions:
            return response_text
        preface = "\n".join(f"Suggestion: {suggestion.text}" for suggestion in suggestions)
        return f"{preface}\n\n{response_text}" if response_text else preface

    def _build_proxy_prompt(self, message_text: str, rendered_context: str) -> str:
        if not rendered_context.strip():
            return message_text
        return f"[CONTEXT]\n{rendered_context}\n\n[USER MESSAGE]\n{message_text}"

    def _build_planner_prompt(self, message_text: str, rendered_context: str) -> str:
        if not rendered_context.strip():
            return message_text
        return f"[CONTEXT]\n{rendered_context}\n\n[USER REQUEST]\n{message_text}"

    def _route_response_text(self, routed: RouteDecision) -> str:
        if routed.route == "planner":
            return "I need to plan this request before execution. Planner execution is not available yet."
        return routed.response.message if routed.response is not None else ""

    def _available_skill_names(self) -> list[str]:
        registry = self.turn_context.skill_registry
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
        if self.turn_context.audit is None:
            return
        await self.turn_context.audit.log(event, **data)

    def _get_context_manager(self) -> LiveContextManager | None:
        if self.context_manager is not None:
            return self.context_manager
        return self.turn_context.context_manager

    def _get_suggestion_engine(self) -> SuggestionEngine | None:
        if self.suggestion_engine is not None:
            return self.suggestion_engine
        return self.turn_context.suggestion_engine

    def _get_autonomy_calibrator(self) -> AutonomyCalibrator | None:
        if self.autonomy_calibrator is not None:
            return self.autonomy_calibrator
        return self.turn_context.autonomy_calibrator

    def _ensure_session_id(self) -> str:
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
