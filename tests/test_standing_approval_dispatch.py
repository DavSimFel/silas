from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from silas.approval.manager import LiveApprovalManager
from silas.core.stream import Stream
from silas.core.turn_context import TurnContext
from silas.goals.manager import SilasGoalManager
from silas.models.agents import AgentResponse, InteractionMode, InteractionRegister, RouteDecision
from silas.models.approval import ApprovalDecision, ApprovalScope, ApprovalToken, ApprovalVerdict
from silas.models.goals import Goal, GoalSchedule, StandingApproval
from silas.models.messages import ChannelMessage
from silas.models.skills import SkillDefinition
from silas.models.work import WorkItem, WorkItemStatus
from silas.skills.executor import SkillExecutor
from silas.skills.registry import SkillRegistry
from silas.work.executor import LiveWorkItemExecutor

from tests.fakes import InMemoryChannel, InMemoryWorkItemStore, RunResult


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _standing_token(
    work_item: WorkItem,
    *,
    expires_at: datetime | None = None,
    executions_used: int = 0,
    max_executions: int = 1,
) -> ApprovalToken:
    now = _utc_now()
    return ApprovalToken(
        token_id=f"standing:{work_item.id}",
        plan_hash=work_item.plan_hash(),
        work_item_id=work_item.parent or "goal-parent",
        scope=ApprovalScope.standing,
        verdict=ApprovalVerdict.approved,
        signature=b"standing-signature",
        issued_at=now - timedelta(minutes=1),
        expires_at=expires_at or (now + timedelta(minutes=30)),
        nonce=f"nonce:{work_item.id}",
        conditions={"spawn_policy_hash": work_item.plan_hash()},
        executions_used=executions_used,
        max_executions=max_executions,
    )


class _GoalManagerStub:
    def __init__(self, approvals: dict[tuple[str, str], StandingApproval] | None = None) -> None:
        self._approvals = approvals or {}

    def get_standing_approval(self, goal_id: str, policy_hash: str) -> StandingApproval | None:
        return self._approvals.get((goal_id, policy_hash))


def _standing_approval(
    goal_id: str,
    work_item: WorkItem,
    *,
    expires_at: datetime | None = None,
    uses_remaining: int | None = None,
    executions_used: int = 0,
    max_executions: int = 1,
) -> StandingApproval:
    return StandingApproval(
        approval_id=f"approval:{goal_id}:{work_item.id}",
        goal_id=goal_id,
        policy_hash=work_item.plan_hash(),
        granted_by="owner",
        granted_at=_utc_now() - timedelta(minutes=1),
        expires_at=expires_at,
        max_uses=max(uses_remaining, 1) if uses_remaining is not None else None,
        uses_remaining=uses_remaining,
        approval_token=_standing_token(
            work_item,
            executions_used=executions_used,
            max_executions=max_executions,
        ),
    )


def _spawned_work_item(goal_id: str, work_id: str = "wi-standing") -> WorkItem:
    return WorkItem(
        id=work_id,
        type="task",
        title="Spawned task",
        body="Execute spawned task",
        parent=goal_id,
        spawned_by=goal_id,
        skills=["skill_a"],
    )


def test_standing_approval_found_auto_approves_without_manual_queue() -> None:
    work_item = _spawned_work_item("goal-1")
    approval = _standing_approval("goal-1", work_item)
    goal_manager = _GoalManagerStub({("goal-1", work_item.plan_hash()): approval})
    manager = LiveApprovalManager(goal_manager=goal_manager)

    token = manager.request_approval(work_item, ApprovalScope.full_plan)

    assert token.scope == ApprovalScope.standing
    assert token.token_id == approval.approval_token.token_id
    assert manager.get_review_queue().poll() == []


def test_standing_approval_missing_falls_through_to_manual() -> None:
    work_item = _spawned_work_item("goal-2")
    manager = LiveApprovalManager(goal_manager=_GoalManagerStub())

    token = manager.request_approval(work_item, ApprovalScope.full_plan)

    assert token.scope == ApprovalScope.full_plan
    assert token.verdict == ApprovalVerdict.conditional
    assert len(manager.get_review_queue().poll()) == 1


def test_standing_approval_expired_falls_through_to_manual() -> None:
    work_item = _spawned_work_item("goal-3")
    approval = _standing_approval(
        "goal-3",
        work_item,
        expires_at=_utc_now() - timedelta(seconds=1),
    )
    goal_manager = _GoalManagerStub({("goal-3", work_item.plan_hash()): approval})
    manager = LiveApprovalManager(goal_manager=goal_manager)

    token = manager.request_approval(work_item, ApprovalScope.full_plan)

    assert token.scope == ApprovalScope.full_plan
    assert len(manager.get_review_queue().poll()) == 1


def test_standing_approval_exhausted_falls_through_to_manual() -> None:
    work_item = _spawned_work_item("goal-4")
    approval = _standing_approval(
        "goal-4",
        work_item,
        uses_remaining=0,
        executions_used=1,
        max_executions=1,
    )
    goal_manager = _GoalManagerStub({("goal-4", work_item.plan_hash()): approval})
    manager = LiveApprovalManager(goal_manager=goal_manager)

    token = manager.request_approval(work_item, ApprovalScope.full_plan)

    assert token.scope == ApprovalScope.full_plan
    assert len(manager.get_review_queue().poll()) == 1


class _GoalStore:
    def __init__(self) -> None:
        self.saved: list[WorkItem] = []

    async def save(self, item: WorkItem) -> None:
        self.saved.append(item.model_copy(deep=True))


class _StandingApprovalEngine:
    async def issue_token(
        self,
        work_item: WorkItem,
        decision: ApprovalDecision,
        scope: ApprovalScope = ApprovalScope.full_plan,
    ) -> ApprovalToken:
        now = _utc_now()
        return ApprovalToken(
            token_id=f"issued:{work_item.id}",
            plan_hash=work_item.plan_hash(),
            work_item_id=work_item.id,
            scope=scope,
            verdict=ApprovalVerdict.approved,
            signature=b"issued-signature",
            issued_at=now,
            expires_at=now + timedelta(hours=1),
            nonce=f"nonce:{work_item.id}",
            conditions=dict(decision.conditions),
            max_executions=3,
        )

    async def verify(
        self,
        token: ApprovalToken,
        work_item: WorkItem,
        spawned_task: WorkItem | None = None,
    ) -> tuple[bool, str]:
        del work_item
        if spawned_task is None:
            return False, "standing_requires_spawned_task"
        if token.scope != ApprovalScope.standing:
            return False, "wrong_scope"
        token.executions_used += 1
        return True, "ok"

    async def check(self, token: ApprovalToken, work_item: WorkItem) -> tuple[bool, str]:
        del token, work_item
        return True, "ok"


class _PlannerProxyModel:
    async def run(self, prompt: str) -> RunResult:
        del prompt
        return RunResult(
            output=RouteDecision(
                route="planner",
                reason="planner route",
                response=None,
                interaction_register=InteractionRegister.execution,
                interaction_mode=InteractionMode.act_and_report,
                context_profile="planning",
            )
        )


class _PlannerWithOneActionModel:
    def __init__(self, action: dict[str, object]) -> None:
        self._action = action

    async def run(self, prompt: str) -> RunResult:
        del prompt
        route = RouteDecision(
            route="planner",
            reason="dispatch standing item",
            response=AgentResponse(message="executing"),
            interaction_register=InteractionRegister.execution,
            interaction_mode=InteractionMode.act_and_report,
            context_profile="planning",
        )
        object.__setattr__(route, "plan_actions", [self._action])
        return RunResult(output=route)


class _ApprovalDecisionChannel(InMemoryChannel):
    def __init__(self, verdict: ApprovalVerdict) -> None:
        super().__init__()
        self._verdict = verdict
        self._approval_handler = None
        self.cards: list[dict[str, object]] = []

    def register_approval_response_handler(self, handler) -> None:
        self._approval_handler = handler

    async def send_approval_card(self, recipient_id: str, card: dict[str, object]) -> None:
        del recipient_id
        self.cards.append(card)
        if self._approval_handler is not None:
            await self._approval_handler(card["id"], self._verdict, "owner")


class _AllowAllApprovalVerifier:
    async def check(self, token: ApprovalToken, work_item: WorkItem) -> tuple[bool, str]:
        del token, work_item
        return True, "ok"


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Integration test needs full stream fake wiring — tracked for follow-up")
async def test_goal_standing_approval_dispatch_executes_without_manual_approval() -> None:
    goal_store = _GoalStore()
    goal_manager = SilasGoalManager(
        goals_config=[
            Goal(
                goal_id="goal-integration",
                name="Integration Goal",
                description="Goal that spawns work",
                schedule=GoalSchedule(kind="interval", interval_seconds=300),
                work_template={
                    "id": "spawned-work-item",
                    "type": "task",
                    "title": "Spawned work",
                    "body": "Run standing-approved spawn",
                    "skills": ["skill_a"],
                },
                skills=["skill_a"],
                standing_approval=True,
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
        ],
        work_item_store=goal_store,
        approval_engine=_StandingApprovalEngine(),
    )
    goal = goal_manager.load_goals()[0]
    assert goal.spawn_policy_hash is not None
    await asyncio.to_thread(
        goal_manager.grant_standing_approval,
        goal.goal_id,
        goal.spawn_policy_hash,
        "owner",
        None,
        3,
    )
    run = await asyncio.to_thread(goal_manager.run_goal, goal.goal_id)
    assert run.status == "completed"
    assert goal_store.saved, "goal run should spawn one work item"
    spawned = goal_store.saved[0]
    assert spawned.needs_approval is False
    assert spawned.approval_token is not None

    planner_action = {
        "id": spawned.id,
        "type": spawned.type.value,
        "title": spawned.title,
        "body": spawned.body,
        "parent": spawned.parent,
        "spawned_by": spawned.spawned_by,
        "skills": list(spawned.skills),
    }

    skill_registry = SkillRegistry()
    skill_registry.register(
        SkillDefinition(
            name="skill_a",
            description="integration skill",
            version="1.0.0",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            requires_approval=False,
            timeout_seconds=5,
        )
    )
    executed_ids: list[str] = []
    skill_executor = SkillExecutor(skill_registry=skill_registry)

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        executed_ids.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    work_store = InMemoryWorkItemStore()
    turn_context = TurnContext(
        scope_id="owner",
        proxy=_PlannerProxyModel(),
        planner=_PlannerWithOneActionModel(planner_action),
        work_executor=LiveWorkItemExecutor(
            skill_executor=skill_executor,
            work_item_store=work_store,
            approval_verifier=_AllowAllApprovalVerifier(),
        ),
        approval_manager=LiveApprovalManager(goal_manager=goal_manager),
        skill_registry=skill_registry,
        skill_executor=skill_executor,
    )
    channel = _ApprovalDecisionChannel(ApprovalVerdict.approved)
    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
        goal_manager=goal_manager,
    )

    summary = await stream._process_turn(
        ChannelMessage(
            channel="web",
            sender_id="owner",
            text="execute standing-approved action",
            timestamp=_utc_now(),
            is_authenticated=True,
        ),
    )

    assert summary == "Plan execution summary: 1 done, 0 failed."
    assert channel.cards == []
    assert executed_ids == [spawned.id]
    stored_item = await work_store.get(spawned.id)
    assert stored_item is not None
    assert stored_item.status == WorkItemStatus.done
