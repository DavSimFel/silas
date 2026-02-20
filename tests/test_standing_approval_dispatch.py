from __future__ import annotations

from datetime import UTC, datetime, timedelta

from silas.gates.approval_manager import LiveApprovalManager
from silas.models.approval import ApprovalScope, ApprovalToken, ApprovalVerdict
from silas.models.goals import StandingApproval
from silas.models.work import WorkItem


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


class _StandingSourceStub:
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


def test_standing_approval_found_returns_token() -> None:
    work_item = _spawned_work_item("goal-1")
    approval = _standing_approval("goal-1", work_item)
    source = _StandingSourceStub({("goal-1", work_item.plan_hash()): approval})
    manager = LiveApprovalManager()

    token = manager.check_standing_approval(work_item, source)

    assert token is not None
    assert token.scope == ApprovalScope.standing
    assert token.token_id == approval.approval_token.token_id


def test_standing_approval_missing_returns_none() -> None:
    work_item = _spawned_work_item("goal-2")
    manager = LiveApprovalManager()

    token = manager.check_standing_approval(work_item, _StandingSourceStub())

    assert token is None


def test_standing_approval_expired_returns_none() -> None:
    work_item = _spawned_work_item("goal-3")
    approval = _standing_approval(
        "goal-3",
        work_item,
        expires_at=_utc_now() - timedelta(seconds=1),
    )
    source = _StandingSourceStub({("goal-3", work_item.plan_hash()): approval})
    manager = LiveApprovalManager()

    token = manager.check_standing_approval(work_item, source)

    assert token is None


def test_standing_approval_exhausted_returns_none() -> None:
    work_item = _spawned_work_item("goal-4")
    approval = _standing_approval(
        "goal-4",
        work_item,
        uses_remaining=0,
        executions_used=1,
        max_executions=1,
    )
    source = _StandingSourceStub({("goal-4", work_item.plan_hash()): approval})
    manager = LiveApprovalManager()

    token = manager.check_standing_approval(work_item, source)

    assert token is None
