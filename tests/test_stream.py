"""Tests for the Stream turn processing (Phase 1a).

Tests cover: routing, taint classification, chronicle injection,
memory retrieval, context profile setting, and edge cases.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from silas.approval import LiveApprovalManager
from silas.core.stream import Stream
from silas.models.agents import InteractionMode, InteractionRegister, RouteDecision
from silas.models.approval import ApprovalVerdict
from silas.models.context import ContextZone
from silas.models.gates import GateLane, GateResult
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import ChannelMessage, TaintLevel
from silas.models.skills import SkillDefinition
from silas.models.work import WorkItemStatus
from silas.skills.executor import SkillExecutor, register_builtin_skills
from silas.skills.registry import SkillRegistry
from silas.work.executor import LiveWorkItemExecutor

from tests.fakes import (
    InMemoryAuditLog,
    InMemoryChannel,
    InMemoryContextManager,
    InMemoryMemoryStore,
    InMemoryWorkItemStore,
    RunResult,
    sample_memory_item,
)


def _msg(text: str, sender_id: str = "owner") -> ChannelMessage:
    return ChannelMessage(
        channel="web",
        sender_id=sender_id,
        text=text,
        timestamp=datetime.now(UTC),
    )


def _stream(
    channel: InMemoryChannel,
    turn_context,
) -> Stream:
    return Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
    )


class BlockingOutputGateRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, TaintLevel, str]] = []

    def evaluate(
        self,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
    ) -> tuple[str, list[GateResult]]:
        self.calls.append((response_text, response_taint, sender_id))
        return response_text, [
            GateResult(
                gate_name="block_all",
                lane=GateLane.policy,
                action="block",
                reason="blocked by test",
            )
        ]


class PlannerRouteModel:
    async def run(self, prompt: str) -> RunResult:
        del prompt
        return RunResult(
            output=RouteDecision(
                route="planner",
                reason="needs planning",
                response=None,
                interaction_register=InteractionRegister.execution,
                interaction_mode=InteractionMode.default_and_offer,
                context_profile="planning",
            )
        )


class PlannerSkillModel:
    async def run(self, prompt: str) -> RunResult:
        del prompt
        return RunResult(
            output=RouteDecision(
                route="planner",
                reason="execute skill",
                response=None,
                interaction_register=InteractionRegister.execution,
                interaction_mode=InteractionMode.default_and_offer,
                context_profile="planning",
                plan_actions=[
                    {
                        "skill_name": "memory_store",
                        "inputs": {"content": "captured by planner", "memory_type": "fact"},
                    }
                ],
            )
        )


class ApprovalDecisionChannel(InMemoryChannel):
    def __init__(self, verdict: ApprovalVerdict) -> None:
        super().__init__()
        self._verdict = verdict
        self.cards: list[dict[str, object]] = []
        self._approval_handler = None

    def register_approval_response_handler(self, handler) -> None:
        self._approval_handler = handler

    async def send_approval_card(self, recipient_id: str, card: dict[str, object]) -> None:
        del recipient_id
        self.cards.append(card)
        if self._approval_handler is not None:
            await self._approval_handler(card["id"], self._verdict, "owner")


@pytest.mark.asyncio
async def test_process_turn_returns_echo_response(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    stream = _stream(channel, turn_context)
    result = await stream._process_turn(_msg("hello world"))
    assert result == "echo: hello world"


@pytest.mark.asyncio
async def test_response_sent_to_channel(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("test"))
    assert len(channel.outgoing) == 1
    assert channel.outgoing[0]["text"] == "echo: test"
    assert channel.outgoing[0]["recipient_id"] == "owner"


@pytest.mark.asyncio
async def test_chronicle_zone_populated(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
) -> None:
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hello"))
    chronicle = context_manager.get_zone("owner", ContextZone.chronicle)
    assert len(chronicle) == 2  # user message + agent response
    assert "[owner]" in chronicle[0].content
    assert "hello" in chronicle[0].content
    assert "Silas:" in chronicle[1].content


@pytest.mark.asyncio
async def test_owner_taint_classification(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
) -> None:
    """Owner sender_id should produce owner taint."""
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hi", sender_id="owner"))
    chronicle = context_manager.get_zone("owner", ContextZone.chronicle)
    assert chronicle[0].taint == TaintLevel.owner


@pytest.mark.asyncio
async def test_external_taint_classification(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
) -> None:
    """Non-owner sender should produce external taint."""
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hi", sender_id="stranger"))
    chronicle = context_manager.get_zone("owner", ContextZone.chronicle)
    assert chronicle[0].taint == TaintLevel.external


@pytest.mark.asyncio
async def test_memory_retrieval_injected(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
    memory_store: InMemoryMemoryStore,
) -> None:
    """Matching memory items should appear in the memory zone."""
    await memory_store.store(sample_memory_item("m1", "hello world context"))
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hello"))
    memory_zone = context_manager.get_zone("owner", ContextZone.memory)
    assert len(memory_zone) == 1
    assert memory_zone[0].content == "hello world context"


@pytest.mark.asyncio
async def test_no_memory_match_empty_zone(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
    memory_store: InMemoryMemoryStore,
) -> None:
    """No matching memories should leave memory zone empty."""
    await memory_store.store(sample_memory_item("m1", "completely unrelated"))
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("xyz123"))
    memory_zone = context_manager.get_zone("owner", ContextZone.memory)
    assert len(memory_zone) == 0


@pytest.mark.asyncio
async def test_auto_retrieval_deduplicates_keyword_and_entity_matches(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
    memory_store: InMemoryMemoryStore,
) -> None:
    await memory_store.store(
        MemoryItem(
            memory_id="m-entity-1",
            content="notes for @alice",
            memory_type=MemoryType.entity,
            taint=TaintLevel.owner,
            source_kind="test",
            entity_refs=["alice"],
        )
    )

    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("@alice"))

    memory_zone = context_manager.get_zone("owner", ContextZone.memory)
    injected_ids = [item.ctx_id for item in memory_zone]
    assert injected_ids.count("memory:m-entity-1") == 1
    assert memory_store.incremented_ids.count("m-entity-1") == 1


@pytest.mark.asyncio
async def test_process_turn_stores_raw_memory_with_session_id(
    channel: InMemoryChannel,
    turn_context,
    memory_store: InMemoryMemoryStore,
) -> None:
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("persist this turn"))

    raw_items = [
        item for item in memory_store.items.values()
        if item.source_kind == "conversation_raw"
    ]
    assert len(raw_items) == 1
    assert raw_items[0].session_id == stream.session_id
    assert raw_items[0].session_id is not None


@pytest.mark.asyncio
async def test_rehydrate_loads_recent_memories_for_current_session(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
    memory_store: InMemoryMemoryStore,
) -> None:
    await memory_store.store(
        MemoryItem(
            memory_id="rehydrate-sess-1",
            content="session memory one",
            memory_type=MemoryType.fact,
            taint=TaintLevel.owner,
            source_kind="test",
            session_id="session-a",
        )
    )
    await memory_store.store(
        MemoryItem(
            memory_id="rehydrate-sess-2",
            content="session memory two",
            memory_type=MemoryType.fact,
            taint=TaintLevel.owner,
            source_kind="test",
            session_id="session-a",
        )
    )
    await memory_store.store(
        MemoryItem(
            memory_id="rehydrate-other",
            content="other session memory",
            memory_type=MemoryType.fact,
            taint=TaintLevel.owner,
            source_kind="test",
            session_id="session-b",
        )
    )

    stream = _stream(channel, turn_context)
    stream.session_id = "session-a"
    await stream._rehydrate()

    memory_zone = context_manager.get_zone("owner", ContextZone.memory)
    memory_ids = {item.ctx_id for item in memory_zone}
    assert "memory:session:rehydrate-sess-1" in memory_ids
    assert "memory:session:rehydrate-sess-2" in memory_ids
    assert "memory:session:rehydrate-other" not in memory_ids


@pytest.mark.asyncio
async def test_turn_number_increments(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    stream = _stream(channel, turn_context)
    assert turn_context.turn_number == 0
    await stream._process_turn(_msg("first"))
    assert turn_context.turn_number == 1
    await stream._process_turn(_msg("second"))
    assert turn_context.turn_number == 2


@pytest.mark.asyncio
async def test_context_profile_set_after_routing(
    channel: InMemoryChannel,
    turn_context,
    context_manager: InMemoryContextManager,
) -> None:
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hello"))
    assert context_manager.profile_by_scope.get("owner") == "conversation"


@pytest.mark.asyncio
async def test_audit_events_logged(
    channel: InMemoryChannel,
    turn_context,
    audit_log: InMemoryAuditLog,
) -> None:
    stream = _stream(channel, turn_context)
    await stream._process_turn(_msg("hi"))
    event_names = [e["event"] for e in audit_log.events]
    assert "turn_processed" in event_names


@pytest.mark.asyncio
async def test_output_gate_block_sanitizes_response(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    gate_runner = BlockingOutputGateRunner()
    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
        output_gate_runner=gate_runner,
    )

    result = await stream._process_turn(_msg("hello", sender_id="stranger"))

    assert result == "I cannot share that"
    assert channel.outgoing[0]["text"] == "I cannot share that"
    assert gate_runner.calls == [("echo: hello", TaintLevel.external, "stranger")]


@pytest.mark.asyncio
async def test_planner_route_response_runs_through_output_gates(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    turn_context.proxy = PlannerRouteModel()
    gate_runner = BlockingOutputGateRunner()
    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
        output_gate_runner=gate_runner,
    )

    result = await stream._process_turn(_msg("build a 5-step plan"))

    assert gate_runner.calls[0][0] == "I need to plan this request before execution. Planner execution is not available yet."
    assert result == "I cannot share that"
    assert channel.outgoing[0]["text"] == "I cannot share that"


class PlannerRouteWithPlanActionsModel:
    async def run(self, prompt: str) -> RunResult:
        del prompt
        routed = RouteDecision(
            route="planner",
            reason="execute plan actions",
            response=None,
            interaction_register=InteractionRegister.execution,
            interaction_mode=InteractionMode.act_and_report,
            context_profile="planning",
        )
        object.__setattr__(
            routed,
            "plan_actions",
            [
                {"id": "plan-a", "type": "task", "title": "Run first action", "body": "Execute first planner action.", "needs_approval": False, "skills": ["skill_a"]},
                {"id": "plan-b", "type": "task", "title": "Run second action", "body": "Execute second planner action.", "needs_approval": False, "skills": ["skill_b"], "depends_on": ["plan-a"]},
            ],
        )
        return RunResult(output=routed)


@pytest.mark.asyncio
async def test_planner_route_executes_plan_actions_and_returns_summary(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    turn_context.proxy = PlannerRouteWithPlanActionsModel()
    skill_registry = SkillRegistry()
    for name in ("skill_a", "skill_b"):
        skill_registry.register(SkillDefinition(name=name, description=f"test {name}", version="1.0.0", input_schema={"type": "object"}, output_schema={"type": "object"}, requires_approval=False, timeout_seconds=5))
    skill_executor = SkillExecutor(skill_registry=skill_registry)
    execution_order: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        execution_order.append(str(inputs["work_item_id"]))
        return {"ok": True}

    async def _skill_b(inputs: dict[str, object]) -> dict[str, object]:
        execution_order.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    skill_executor.register_handler("skill_b", _skill_b)
    work_store = InMemoryWorkItemStore()
    turn_context.skill_registry = skill_registry
    turn_context.skill_executor = skill_executor
    turn_context.work_executor = LiveWorkItemExecutor(skill_executor=skill_executor, work_item_store=work_store)

    stream = _stream(channel, turn_context)
    result = await stream._process_turn(_msg("build and run a plan"))
    assert result == "Plan execution summary: 2 done, 0 failed."
    assert execution_order == ["plan-a", "plan-b"]
    plan_a = await work_store.get("plan-a")
    plan_b = await work_store.get("plan-b")
    assert plan_a is not None
    assert plan_a.status == WorkItemStatus.done
    assert plan_b is not None
    assert plan_b.status == WorkItemStatus.done


@pytest.mark.asyncio
async def test_no_proxy_raises(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    """Stream must raise if proxy is not set."""
    turn_context.proxy = None
    stream = _stream(channel, turn_context)
    with pytest.raises(RuntimeError, match="proxy"):
        await stream._process_turn(_msg("hello"))


@pytest.mark.asyncio
async def test_planner_skill_requires_approval_and_executes_on_approve(
    turn_context,
    memory_store: InMemoryMemoryStore,
    audit_log: InMemoryAuditLog,
) -> None:
    channel = ApprovalDecisionChannel(ApprovalVerdict.approved)
    turn_context.proxy = PlannerSkillModel()
    turn_context.approval_manager = LiveApprovalManager()

    skill_registry = SkillRegistry()
    register_builtin_skills(skill_registry)
    turn_context.skill_registry = skill_registry
    turn_context.skill_executor = SkillExecutor(
        skill_registry=skill_registry,
        memory_store=memory_store,
    )

    stream = _stream(channel, turn_context)
    result = await stream._process_turn(_msg("please store memory"))

    assert "Executed skill 'memory_store'." in result
    assert len(channel.cards) == 1
    assert any(item.source_kind == "skill:memory_store" for item in memory_store.items.values())
    event_names = [event["event"] for event in audit_log.events]
    assert "approval_requested" in event_names


@pytest.mark.asyncio
async def test_planner_skill_skips_execution_when_declined(
    turn_context,
    memory_store: InMemoryMemoryStore,
    audit_log: InMemoryAuditLog,
) -> None:
    channel = ApprovalDecisionChannel(ApprovalVerdict.declined)
    turn_context.proxy = PlannerSkillModel()
    turn_context.approval_manager = LiveApprovalManager()

    skill_registry = SkillRegistry()
    register_builtin_skills(skill_registry)
    turn_context.skill_registry = skill_registry
    turn_context.skill_executor = SkillExecutor(
        skill_registry=skill_registry,
        memory_store=memory_store,
    )

    stream = _stream(channel, turn_context)
    result = await stream._process_turn(_msg("please store memory"))

    assert "Skipped skill 'memory_store': approval declined." in result
    assert len(channel.cards) == 1
    assert not any(item.source_kind == "skill:memory_store" for item in memory_store.items.values())
    event_names = [event["event"] for event in audit_log.events]
    assert "skill_execution_skipped_approval" in event_names
