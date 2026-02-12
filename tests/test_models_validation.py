"""Comprehensive model validation tests for Phase 1a.

Tests cover happy paths, boundary conditions, and error cases
for all Pydantic models per specs.md Section 3.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from silas.models.agents import (
    AgentResponse,
    InteractionMode,
    InteractionRegister,
    MemoryOp,
    MemoryOpType,
    MemoryQuery,
    MemoryQueryStrategy,
    PlanAction,
    PlanActionType,
    RouteDecision,
)
from silas.models.approval import (
    ApprovalDecision,
    ApprovalScope,
    ApprovalToken,
    ApprovalVerdict,
)
from silas.models.context import (
    ContextItem,
    ContextProfile,
    ContextZone,
    TokenBudget,
)
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import (
    ChannelMessage,
    SignedMessage,
    TaintLevel,
    signed_message_canonical_bytes,
    utc_now,
)
from silas.models.work import (
    Budget,
    BudgetUsed,
    Expectation,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestChannelMessage:
    def test_valid_message(self) -> None:
        msg = ChannelMessage(channel="web", sender_id="user1", text="hello")
        assert msg.channel == "web"
        assert msg.timestamp.tzinfo is not None

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            ChannelMessage(
                channel="web",
                sender_id="u",
                text="hi",
                timestamp=datetime(2026, 1, 1),
            )

    def test_default_attachments_empty(self) -> None:
        msg = ChannelMessage(channel="web", sender_id="u", text="hi")
        assert msg.attachments == []
        assert msg.reply_to is None


class TestSignedMessage:
    def test_default_taint_is_external(self) -> None:
        msg = ChannelMessage(channel="web", sender_id="u", text="hi")
        signed = SignedMessage(message=msg, signature=b"sig", nonce="n1")
        assert signed.taint == TaintLevel.external

    def test_canonical_bytes_deterministic(self) -> None:
        msg = ChannelMessage(
            channel="web",
            sender_id="u",
            text="hello",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        b1 = signed_message_canonical_bytes(msg, "nonce1")
        b2 = signed_message_canonical_bytes(msg, "nonce1")
        assert b1 == b2

    def test_canonical_bytes_sorted_keys(self) -> None:
        """Spec requires JSON with sorted keys and no insignificant whitespace."""
        msg = ChannelMessage(
            channel="web",
            sender_id="u",
            text="test",
            timestamp=datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC),
        )
        raw = signed_message_canonical_bytes(msg, "n1")
        import json

        parsed = json.loads(raw)
        # Keys must be sorted: nonce, text, timestamp
        assert list(parsed.keys()) == ["nonce", "text", "timestamp"]
        # No spaces in raw bytes
        assert b" " not in raw

    def test_canonical_bytes_different_nonce(self) -> None:
        msg = ChannelMessage(
            channel="web",
            sender_id="u",
            text="hello",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        b1 = signed_message_canonical_bytes(msg, "nonce1")
        b2 = signed_message_canonical_bytes(msg, "nonce2")
        assert b1 != b2


# ---------------------------------------------------------------------------
# Agent Response & RouteDecision
# ---------------------------------------------------------------------------


class TestAgentResponse:
    def test_exactly_three_queries_allowed(self) -> None:
        """Boundary: 3 queries should be accepted (max is 3)."""
        queries = [
            MemoryQuery(strategy=MemoryQueryStrategy.keyword, query=f"q{i}")
            for i in range(3)
        ]
        resp = AgentResponse(message="ok", memory_queries=queries)
        assert len(resp.memory_queries) == 3

    def test_four_queries_rejected(self) -> None:
        queries = [
            MemoryQuery(strategy=MemoryQueryStrategy.keyword, query=f"q{i}")
            for i in range(4)
        ]
        with pytest.raises(ValidationError, match="3"):
            AgentResponse(message="ok", memory_queries=queries)

    def test_zero_queries_allowed(self) -> None:
        resp = AgentResponse(message="ok")
        assert resp.memory_queries == []

    def test_default_needs_approval_true(self) -> None:
        resp = AgentResponse(message="ok")
        assert resp.needs_approval is True


class TestRouteDecision:
    def test_direct_route_requires_response(self) -> None:
        with pytest.raises(ValidationError, match="response is required"):
            RouteDecision(
                route="direct",
                reason="test",
                response=None,
                interaction_register=InteractionRegister.status,
                interaction_mode=InteractionMode.default_and_offer,
                context_profile="conversation",
            )

    def test_planner_route_requires_no_response(self) -> None:
        with pytest.raises(ValidationError, match="response must be None"):
            RouteDecision(
                route="planner",
                reason="test",
                response=AgentResponse(message="oops"),
                interaction_register=InteractionRegister.status,
                interaction_mode=InteractionMode.default_and_offer,
                context_profile="conversation",
            )

    def test_planner_route_valid(self) -> None:
        rd = RouteDecision(
            route="planner",
            reason="complex task",
            response=None,
            interaction_register=InteractionRegister.execution,
            interaction_mode=InteractionMode.act_and_report,
            context_profile="coding",
        )
        assert rd.route == "planner"

    def test_empty_context_profile_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-empty"):
            RouteDecision(
                route="planner",
                reason="test",
                response=None,
                interaction_register=InteractionRegister.status,
                interaction_mode=InteractionMode.default_and_offer,
                context_profile="",
            )

    def test_unknown_context_profile_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown context profile"):
            RouteDecision(
                route="planner",
                reason="test",
                response=None,
                interaction_register=InteractionRegister.status,
                interaction_mode=InteractionMode.default_and_offer,
                context_profile="nonexistent_profile",
            )


# ---------------------------------------------------------------------------
# MemoryOp Validators
# ---------------------------------------------------------------------------


class TestMemoryOp:
    def test_store_requires_content(self) -> None:
        with pytest.raises(ValidationError, match="content is required"):
            MemoryOp(op=MemoryOpType.store, content=None)

    def test_store_valid(self) -> None:
        op = MemoryOp(op=MemoryOpType.store, content="some fact")
        assert op.content == "some fact"

    def test_update_requires_memory_id_and_content(self) -> None:
        with pytest.raises(ValidationError):
            MemoryOp(op=MemoryOpType.update, content="new", memory_id=None)
        with pytest.raises(ValidationError):
            MemoryOp(op=MemoryOpType.update, content=None, memory_id="m1")

    def test_delete_requires_memory_id(self) -> None:
        with pytest.raises(ValidationError, match="memory_id is required"):
            MemoryOp(op=MemoryOpType.delete)

    def test_link_requires_all_fields(self) -> None:
        with pytest.raises(ValidationError):
            MemoryOp(op=MemoryOpType.link, memory_id="m1", link_to="m2")
        # Valid link
        op = MemoryOp(
            op=MemoryOpType.link,
            memory_id="m1",
            link_to="m2",
            link_type="caused_by",
        )
        assert op.link_type == "caused_by"


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


class TestContextProfile:
    def test_valid_profile(self) -> None:
        p = ContextProfile(name="test", chronicle_pct=0.40, memory_pct=0.20, workspace_pct=0.20)
        assert p.chronicle_pct == 0.40

    def test_sum_exactly_0_80_allowed(self) -> None:
        """Boundary: sum == 0.80 should be valid (spec says <= 0.80)."""
        p = ContextProfile(name="exact", chronicle_pct=0.40, memory_pct=0.20, workspace_pct=0.20)
        assert p.chronicle_pct + p.memory_pct + p.workspace_pct == 0.80

    def test_sum_exceeds_0_80_rejected(self) -> None:
        with pytest.raises(ValidationError, match="0.80"):
            ContextProfile(name="too-big", chronicle_pct=0.50, memory_pct=0.20, workspace_pct=0.20)

    def test_negative_pct_rejected(self) -> None:
        with pytest.raises(ValidationError, match="range"):
            ContextProfile(name="neg", chronicle_pct=-0.1, memory_pct=0.2, workspace_pct=0.2)

    def test_pct_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError, match="range"):
            ContextProfile(name="over", chronicle_pct=1.1, memory_pct=0.0, workspace_pct=0.0)

    def test_all_zero_valid(self) -> None:
        p = ContextProfile(name="empty", chronicle_pct=0.0, memory_pct=0.0, workspace_pct=0.0)
        assert p.chronicle_pct + p.memory_pct + p.workspace_pct == 0.0


class TestTokenBudget:
    def test_allocable_budget_calculation(self) -> None:
        tb = TokenBudget(total=180_000, system_max=8_000)
        # system zone uses 5000 tokens
        assert tb.allocable_budget(5_000) == 175_000
        # system zone exceeds cap — capped at system_max
        assert tb.allocable_budget(10_000) == 172_000

    def test_zone_budget_calculation(self) -> None:
        tb = TokenBudget(total=100_000, system_max=10_000)
        profile = ContextProfile(name="t", chronicle_pct=0.40, memory_pct=0.20, workspace_pct=0.20)
        # allocable = 100k - 10k = 90k
        assert tb.zone_budget(ContextZone.chronicle, profile, 10_000) == 36_000
        assert tb.zone_budget(ContextZone.memory, profile, 10_000) == 18_000
        assert tb.zone_budget(ContextZone.workspace, profile, 10_000) == 18_000

    def test_skill_metadata_budget_pct_max(self) -> None:
        with pytest.raises(ValidationError, match="skill_metadata_budget_pct"):
            TokenBudget(skill_metadata_budget_pct=0.15)

    def test_default_profile_must_exist_in_profiles(self) -> None:
        profile = ContextProfile(name="coding", chronicle_pct=0.2, memory_pct=0.2, workspace_pct=0.2)
        with pytest.raises(ValidationError, match="default_profile"):
            TokenBudget(
                profiles={"coding": profile},
                default_profile="nonexistent",
            )


class TestContextItem:
    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            ContextItem(
                ctx_id="c1",
                zone=ContextZone.chronicle,
                content="text",
                token_count=10,
                created_at=datetime(2026, 1, 1),
                turn_number=1,
                source="test",
                kind="message",
            )

    def test_defaults(self) -> None:
        item = ContextItem(
            ctx_id="c1",
            zone=ContextZone.memory,
            content="x",
            token_count=1,
            turn_number=1,
            source="test",
            kind="memory",
        )
        assert item.relevance == 1.0
        assert item.masked is False
        assert item.pinned is False
        assert item.taint == TaintLevel.external


# ---------------------------------------------------------------------------
# Work Items, Budget, Expectations
# ---------------------------------------------------------------------------


class TestBudgetUsed:
    def test_exceeds_at_exact_limit(self) -> None:
        """Spec: >= semantics — reaching exact limit counts as exhausted."""
        budget = Budget(max_tokens=100, max_cost_usd=1.0, max_wall_time_seconds=60, max_attempts=3, max_planner_calls=2)
        used = BudgetUsed(tokens=100)
        assert used.exceeds(budget) is True

    def test_not_exceeded_below_limit(self) -> None:
        budget = Budget(max_tokens=100)
        used = BudgetUsed(tokens=99)
        assert used.exceeds(budget) is False

    def test_exceeds_on_cost(self) -> None:
        budget = Budget(max_cost_usd=2.0)
        used = BudgetUsed(cost_usd=2.0)
        assert used.exceeds(budget) is True

    def test_exceeds_on_wall_time(self) -> None:
        budget = Budget(max_wall_time_seconds=1800)
        used = BudgetUsed(wall_time_seconds=1800.0)
        assert used.exceeds(budget) is True

    def test_exceeds_on_attempts(self) -> None:
        budget = Budget(max_attempts=5)
        used = BudgetUsed(attempts=5)
        assert used.exceeds(budget) is True

    def test_exceeds_on_planner_calls(self) -> None:
        budget = Budget(max_planner_calls=3)
        used = BudgetUsed(planner_calls=3)
        assert used.exceeds(budget) is True

    def test_merge_aggregates_all_fields(self) -> None:
        """Spec: merge MUST aggregate ALL fields including attempts and executor_runs."""
        parent = BudgetUsed(tokens=100, cost_usd=0.5, wall_time_seconds=10.0, attempts=1, planner_calls=1, executor_runs=1)
        child = BudgetUsed(tokens=200, cost_usd=1.0, wall_time_seconds=20.0, attempts=2, planner_calls=1, executor_runs=3)
        result = parent.merge(child)
        assert result is parent  # mutates in place
        assert result.tokens == 300
        assert result.cost_usd == 1.5
        assert result.wall_time_seconds == 30.0
        assert result.attempts == 3
        assert result.planner_calls == 2
        assert result.executor_runs == 4

    def test_fresh_budget_does_not_exceed(self) -> None:
        budget = Budget()
        used = BudgetUsed()
        assert used.exceeds(budget) is False


class TestExpectation:
    def test_exactly_one_predicate_required(self) -> None:
        """Zero predicates should be rejected."""
        with pytest.raises(ValidationError, match="exactly one"):
            Expectation()

    def test_multiple_predicates_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            Expectation(contains="ok", regex="ok")

    def test_single_predicate_valid(self) -> None:
        e = Expectation(exit_code=0)
        assert e.exit_code == 0

    def test_each_predicate_individually(self) -> None:
        assert Expectation(exit_code=0).exit_code == 0
        assert Expectation(equals="ok").equals == "ok"
        assert Expectation(contains="ok").contains == "ok"
        assert Expectation(regex=r"\d+").regex == r"\d+"
        assert Expectation(output_lt=5.0).output_lt == 5.0
        assert Expectation(output_gt=1.0).output_gt == 1.0
        assert Expectation(file_exists="/tmp/x").file_exists == "/tmp/x"
        assert Expectation(not_empty=True).not_empty is True

    def test_not_empty_false_counts_as_no_predicate(self) -> None:
        """Spec: not_empty must be True to count as set."""
        with pytest.raises(ValidationError, match="exactly one"):
            Expectation(not_empty=False)


class TestWorkItem:
    def _make_work_item(self, **kwargs: object) -> WorkItem:
        defaults = {
            "id": "wi-1",
            "type": WorkItemType.task,
            "title": "Test task",
            "body": "Do the thing",
        }
        defaults.update(kwargs)
        return WorkItem(**defaults)

    def test_task_must_be_ephemeral(self) -> None:
        with pytest.raises(ValidationError, match="ephemeral"):
            self._make_work_item(type=WorkItemType.task, agent="stream")

    def test_project_must_be_ephemeral(self) -> None:
        with pytest.raises(ValidationError, match="ephemeral"):
            self._make_work_item(type=WorkItemType.project, agent="stream")

    def test_goal_always_on_defaults_to_stream(self) -> None:
        wi = self._make_work_item(
            type=WorkItemType.goal,
            schedule="always_on",
            agent="ephemeral",
        )
        assert wi.agent == "stream"

    def test_goal_with_cron_can_be_ephemeral(self) -> None:
        wi = self._make_work_item(
            type=WorkItemType.goal,
            schedule="*/30 * * * *",
            agent="ephemeral",
        )
        assert wi.agent == "ephemeral"

    def test_plan_hash_excludes_mutable_fields(self) -> None:
        """Plan hash must be stable when runtime-managed fields change."""
        wi = self._make_work_item()
        hash1 = wi.plan_hash()

        wi.status = WorkItemStatus.running
        wi.attempts = 3
        wi.budget_used = BudgetUsed(tokens=5000)
        hash2 = wi.plan_hash()

        assert hash1 == hash2, "plan_hash must exclude mutable/runtime fields"

    def test_plan_hash_changes_on_body_change(self) -> None:
        wi1 = self._make_work_item(body="version 1")
        wi2 = self._make_work_item(body="version 2")
        assert wi1.plan_hash() != wi2.plan_hash()

    def test_naive_created_at_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            self._make_work_item(created_at=datetime(2026, 1, 1))

    def test_default_status_is_pending(self) -> None:
        wi = self._make_work_item()
        assert wi.status == WorkItemStatus.pending


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


def _make_token(**kwargs: object) -> ApprovalToken:
    now = utc_now()
    defaults = {
        "token_id": "tok-1",
        "plan_hash": "abc123",
        "work_item_id": "item-1",
        "scope": ApprovalScope.full_plan,
        "verdict": ApprovalVerdict.approved,
        "signature": b"test-signature",
        "issued_at": now,
        "expires_at": now + timedelta(minutes=30),
        "nonce": "n-1",
    }
    defaults.update(kwargs)
    return ApprovalToken(**defaults)


class TestApprovalToken:
    def test_base64_roundtrip(self) -> None:
        token = _make_token(signature=b"binary-sig-data\x00\xff")
        dumped = token.model_dump(mode="json")
        assert isinstance(dumped["signature"], str)
        loaded = ApprovalToken.model_validate(dumped)
        assert loaded.signature == b"binary-sig-data\x00\xff"

    def test_expires_before_issued_rejected(self) -> None:
        now = utc_now()
        with pytest.raises(ValidationError, match="expires_at must be greater"):
            _make_token(issued_at=now, expires_at=now - timedelta(minutes=1))

    def test_expires_equal_to_issued_rejected(self) -> None:
        now = utc_now()
        with pytest.raises(ValidationError, match="expires_at must be greater"):
            _make_token(issued_at=now, expires_at=now)

    def test_standing_scope_requires_spawn_policy_hash(self) -> None:
        with pytest.raises(ValidationError, match="spawn_policy_hash"):
            _make_token(scope=ApprovalScope.standing, conditions={})

    def test_standing_scope_with_spawn_policy_hash(self) -> None:
        token = _make_token(
            scope=ApprovalScope.standing,
            conditions={"spawn_policy_hash": "abc"},
            max_executions=10,
        )
        assert token.scope == ApprovalScope.standing

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            _make_token(expires_at=datetime(2099, 1, 1))


class TestApprovalDecision:
    def test_default_strength_is_tap(self) -> None:
        d = ApprovalDecision(verdict=ApprovalVerdict.approved)
        assert d.approval_strength == "tap"

    def test_only_tap_allowed(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalDecision(verdict=ApprovalVerdict.approved, approval_strength="biometric")


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class TestMemoryItem:
    def test_memory_types(self) -> None:
        for mt in MemoryType:
            item = MemoryItem(
                memory_id="m1",
                content="test",
                memory_type=mt,
                taint=TaintLevel.owner,
                source_kind="test",
            )
            assert item.memory_type == mt


# ---------------------------------------------------------------------------
# PlanAction
# ---------------------------------------------------------------------------


class TestPlanAction:
    def test_all_action_types(self) -> None:
        for at in PlanActionType:
            pa = PlanAction(action=at)
            assert pa.action == at

    def test_continuation_of_field(self) -> None:
        pa = PlanAction(action=PlanActionType.revise, continuation_of="wi-old")
        assert pa.continuation_of == "wi-old"
