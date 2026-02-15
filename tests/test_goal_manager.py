"""Tests for GoalManager and EventRouter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from silas.core.event_router import EventRouter, WebhookEvent
from silas.core.goal_manager import GoalManager
from silas.models.goals import (
    Goal,
    GoalRun,
    GoalSchedule,
    GoalSubscription,
    ReportingConfig,
    StandingApproval,
)


def _make_goal(
    goal_id: str = "g1",
    subscriptions: list[GoalSubscription] | None = None,
    standing_approvals: list[str] | None = None,
    enabled: bool = True,
    urgency: str = "background",
) -> Goal:
    return Goal(
        goal_id=goal_id,
        name=f"Goal {goal_id}",
        description=f"Description for {goal_id}",
        schedule=GoalSchedule(kind="interval", interval_seconds=3600),
        subscriptions=subscriptions or [],
        standing_approvals=standing_approvals or [],
        enabled=enabled,
        urgency=urgency,
    )


def _make_sub(
    sub_id: str = "s1",
    source: str = "github",
    event_type: str = "push",
    filter: dict | None = None,
) -> GoalSubscription:
    return GoalSubscription(
        sub_id=sub_id,
        source=source,
        event_type=event_type,
        filter=filter or {},
    )


def _make_approval(
    approval_id: str = "a1",
    goal_id: str = "g1",
    expires_at: datetime | None = None,
    max_uses: int | None = None,
) -> StandingApproval:
    now = datetime.now(UTC)
    return StandingApproval(
        approval_id=approval_id,
        goal_id=goal_id,
        policy_hash="hash123",
        granted_by="owner",
        granted_at=now,
        expires_at=expires_at,
        max_uses=max_uses,
    )


# ── CRUD ────────────────────────────────────────────────────────


class TestGoalManagerCRUD:
    def test_register_and_get(self) -> None:
        mgr = GoalManager()
        goal = _make_goal()
        mgr.register(goal)
        assert mgr.get("g1") is goal
        assert mgr.get("nonexistent") is None

    def test_unregister(self) -> None:
        mgr = GoalManager()
        mgr.register(_make_goal())
        assert mgr.unregister("g1") is True
        assert mgr.unregister("g1") is False
        assert mgr.get("g1") is None

    def test_list_goals(self) -> None:
        mgr = GoalManager()
        mgr.register(_make_goal("g1", enabled=True))
        mgr.register(_make_goal("g2", enabled=False))
        assert len(mgr.list_goals()) == 2
        assert len(mgr.list_goals(enabled_only=True)) == 1

    def test_record_and_get_run(self) -> None:
        mgr = GoalManager()
        run = GoalRun(run_id="r1", goal_id="g1")
        mgr.record_run(run)
        assert mgr.get_run("r1") is run
        assert mgr.get_run("missing") is None


# ── Event matching ──────────────────────────────────────────────


class TestEventMatching:
    def test_exact_match(self) -> None:
        mgr = GoalManager()
        sub = _make_sub(source="github", event_type="push")
        mgr.register(_make_goal(subscriptions=[sub]))
        matched = mgr.match_event("github", "push")
        assert len(matched) == 1
        assert matched[0].goal_id == "g1"

    def test_no_match_wrong_source(self) -> None:
        mgr = GoalManager()
        sub = _make_sub(source="github", event_type="push")
        mgr.register(_make_goal(subscriptions=[sub]))
        assert mgr.match_event("gitlab", "push") == []

    def test_no_match_wrong_event(self) -> None:
        mgr = GoalManager()
        sub = _make_sub(source="github", event_type="push")
        mgr.register(_make_goal(subscriptions=[sub]))
        assert mgr.match_event("github", "pull_request") == []

    def test_wildcard_event_type(self) -> None:
        mgr = GoalManager()
        sub = _make_sub(source="github", event_type="push*")
        mgr.register(_make_goal(subscriptions=[sub]))
        assert len(mgr.match_event("github", "push")) == 1
        assert len(mgr.match_event("github", "push_tag")) == 1
        assert len(mgr.match_event("github", "pull_request")) == 0

    def test_filter_match(self) -> None:
        mgr = GoalManager()
        sub = _make_sub(source="github", event_type="push", filter={"branch": "main"})
        mgr.register(_make_goal(subscriptions=[sub]))
        assert len(mgr.match_event("github", "push", {"branch": "main"})) == 1
        assert len(mgr.match_event("github", "push", {"branch": "dev"})) == 0

    def test_filter_requires_data(self) -> None:
        mgr = GoalManager()
        sub = _make_sub(source="github", event_type="push", filter={"branch": "main"})
        mgr.register(_make_goal(subscriptions=[sub]))
        assert mgr.match_event("github", "push", None) == []

    def test_disabled_goal_not_matched(self) -> None:
        mgr = GoalManager()
        sub = _make_sub(source="github", event_type="push")
        mgr.register(_make_goal(subscriptions=[sub], enabled=False))
        assert mgr.match_event("github", "push") == []

    def test_inactive_subscription_not_matched(self) -> None:
        mgr = GoalManager()
        sub = _make_sub(source="github", event_type="push")
        sub.active = False
        mgr.register(_make_goal(subscriptions=[sub]))
        assert mgr.match_event("github", "push") == []

    def test_multiple_goals_matched(self) -> None:
        mgr = GoalManager()
        sub1 = _make_sub(sub_id="s1", source="github", event_type="push")
        sub2 = _make_sub(sub_id="s2", source="github", event_type="push")
        mgr.register(_make_goal("g1", subscriptions=[sub1]))
        mgr.register(_make_goal("g2", subscriptions=[sub2]))
        assert len(mgr.match_event("github", "push")) == 2


# ── Standing approvals ──────────────────────────────────────────


class TestStandingApprovals:
    def test_valid_approval(self) -> None:
        mgr = GoalManager()
        approval = _make_approval(expires_at=datetime.now(UTC) + timedelta(hours=1))
        mgr.add_standing_approval(approval)
        goal = _make_goal(standing_approvals=["a1"])
        assert mgr.check_standing_approval(goal) is approval

    def test_expired_approval(self) -> None:
        mgr = GoalManager()
        now = datetime.now(UTC)
        approval = _make_approval(expires_at=now + timedelta(hours=1))
        # Manually expire it after construction to bypass validator
        approval.expires_at = now - timedelta(hours=1)
        mgr.add_standing_approval(approval)
        goal = _make_goal(standing_approvals=["a1"])
        assert mgr.check_standing_approval(goal) is None

    def test_exhausted_approval(self) -> None:
        mgr = GoalManager()
        approval = _make_approval(max_uses=1)
        approval.uses_remaining = 0
        mgr.add_standing_approval(approval)
        goal = _make_goal(standing_approvals=["a1"])
        assert mgr.check_standing_approval(goal) is None

    def test_consume_approval(self) -> None:
        mgr = GoalManager()
        approval = _make_approval(max_uses=3)
        assert approval.uses_remaining == 3
        mgr.consume_approval(approval)
        assert approval.uses_remaining == 2

    def test_no_approval_registered(self) -> None:
        mgr = GoalManager()
        goal = _make_goal(standing_approvals=["nonexistent"])
        assert mgr.check_standing_approval(goal) is None

    def test_approval_wrong_goal(self) -> None:
        mgr = GoalManager()
        approval = _make_approval(goal_id="other_goal")
        mgr.add_standing_approval(approval)
        goal = _make_goal(standing_approvals=["a1"])
        assert mgr.check_standing_approval(goal) is None


# ── Queue injection ─────────────────────────────────────────────


class TestInjectEvent:
    @pytest.mark.anyio
    async def test_inject_without_store(self) -> None:
        mgr = GoalManager(store=None)
        goal = _make_goal(urgency="needs_attention")
        msg = await mgr.inject_event(goal, "github", "push", {"ref": "main"})
        assert msg is not None
        assert msg.message_kind == "user_message"
        assert msg.sender == "runtime"
        assert msg.payload["goal_id"] == "g1"
        assert msg.payload["has_standing_approval"] is False
        assert msg.urgency == "needs_attention"

    @pytest.mark.anyio
    async def test_inject_with_standing_approval(self) -> None:
        mgr = GoalManager()
        approval = _make_approval(
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            max_uses=2,
        )
        mgr.add_standing_approval(approval)
        goal = _make_goal(standing_approvals=["a1"])
        msg = await mgr.inject_event(goal, "github", "push")
        assert msg is not None
        assert msg.payload["has_standing_approval"] is True
        assert approval.uses_remaining == 1


# ── Auto-deactivation ──────────────────────────────────────────


class TestDeactivation:
    def test_deactivate_goal(self) -> None:
        mgr = GoalManager()
        sub = _make_sub()
        goal = _make_goal(subscriptions=[sub])
        mgr.register(goal)
        assert mgr.deactivate_on_completion("g1") is True
        assert goal.enabled is False
        assert sub.active is False

    def test_deactivate_nonexistent(self) -> None:
        mgr = GoalManager()
        assert mgr.deactivate_on_completion("missing") is False


# ── EventRouter ─────────────────────────────────────────────────


class TestEventRouter:
    @pytest.mark.anyio
    async def test_route_event_matches(self) -> None:
        mgr = GoalManager()
        sub = _make_sub(source="stripe", event_type="invoice.paid")
        mgr.register(_make_goal(subscriptions=[sub]))
        router = EventRouter(mgr)
        event = WebhookEvent(source="stripe", event_type="invoice.paid", data={"amount": 100})
        messages = await router.handle_event(event)
        assert len(messages) == 1
        assert messages[0].payload["event_source"] == "stripe"

    @pytest.mark.anyio
    async def test_route_event_no_match(self) -> None:
        mgr = GoalManager()
        router = EventRouter(mgr)
        event = WebhookEvent(source="unknown", event_type="nope")
        messages = await router.handle_event(event)
        assert messages == []


# ── Model tests ─────────────────────────────────────────────────


class TestModels:
    def test_goal_with_new_fields(self) -> None:
        goal = _make_goal(
            subscriptions=[_make_sub()],
            standing_approvals=["a1"],
            urgency="needs_attention",
        )
        assert len(goal.subscriptions) == 1
        assert goal.urgency == "needs_attention"
        assert goal.reporting.on_success is True

    def test_reporting_config_defaults(self) -> None:
        config = ReportingConfig()
        assert config.channel == "owner"
        assert config.summary_style == "brief"

    def test_goal_subscription_validation(self) -> None:
        sub = _make_sub()
        assert sub.active is True
        assert sub.created_at.tzinfo is not None
