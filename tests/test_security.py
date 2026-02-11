"""Security-focused tests for approval, token, nonce, and taint behavior."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from pydantic import ValidationError
from silas.approval.manager import LiveApprovalManager
from silas.core.stream import Stream
from silas.goals.manager import SilasGoalManager
from silas.models.approval import (
    ApprovalScope,
    ApprovalToken,
    ApprovalVerdict,
)
from silas.models.context import ContextZone
from silas.models.goals import Goal, GoalSchedule
from silas.models.messages import ChannelMessage, TaintLevel
from silas.models.work import WorkItem, WorkItemStatus, WorkItemType
from silas.persistence.migrations import run_migrations
from silas.persistence.nonce_store import SQLiteNonceStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _work_item(item_id: str = "wi-sec", *, body: str = "Perform secure operation") -> WorkItem:
    return WorkItem(
        id=item_id,
        type=WorkItemType.task,
        title=f"Security Work Item {item_id}",
        body=body,
    )


def _approval_token(**kwargs: object) -> ApprovalToken:
    now = _utc_now()
    payload: dict[str, object] = {
        "token_id": "token-sec-1",
        "plan_hash": "deadbeef",
        "work_item_id": "wi-sec",
        "scope": ApprovalScope.full_plan,
        "verdict": ApprovalVerdict.approved,
        "signature": b"\x01\x02binary-signature",
        "issued_at": now,
        "expires_at": now + timedelta(minutes=30),
        "nonce": "nonce-sec-1",
    }
    payload.update(kwargs)
    return ApprovalToken.model_validate(payload)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _spawn_policy_hash(
    *,
    failure_context_template: str,
    skills: list[str],
    gates: list[dict[str, object]],
    verify: list[dict[str, object]],
    escalation_config: dict[str, object],
) -> str:
    """Spec-style canonical spawn policy hash helper (Section 3.6)."""
    canonical = {
        "failure_context_template": failure_context_template,
        "skills": sorted(set(skills)),
        "gates": sorted(_canonical_json(gate) for gate in gates),
        "verify": sorted(_canonical_json(check) for check in verify),
        "escalation_config": {
            key: escalation_config[key]
            for key in sorted(escalation_config)
        },
    }
    return hashlib.sha256(_canonical_json(canonical).encode("utf-8")).hexdigest()


def _goal(goal_id: str = "goal-sec") -> Goal:
    now = _utc_now()
    return Goal(
        goal_id=goal_id,
        name=f"Goal {goal_id}",
        description="Run security checks",
        schedule=GoalSchedule(kind="interval", interval_seconds=300),
        work_template={
            "type": "task",
            "title": "Run goal task",
            "body": "Investigate and remediate drift",
        },
        skills=["security"],
        created_at=now,
        updated_at=now,
    )


def _msg(text: str, sender_id: str = "owner") -> ChannelMessage:
    return ChannelMessage(
        channel="web",
        sender_id=sender_id,
        text=text,
        timestamp=_utc_now(),
    )


class _CollectingWorkItemStore:
    def __init__(self) -> None:
        self.saved: list[WorkItem] = []

    async def save(self, item: WorkItem) -> None:
        self.saved.append(item.model_copy(deep=True))


# ---------------------------------------------------------------------------
# 1) Approval Engine (LiveApprovalManager) — spec Section 5.11 (current subset)
# ---------------------------------------------------------------------------


class TestLiveApprovalManager:
    def test_issue_pending_approval_and_list_pending(self) -> None:
        manager = LiveApprovalManager(timeout=timedelta(minutes=10))
        work_item = _work_item("wi-pending")

        token = manager.request_approval(work_item, ApprovalScope.full_plan)

        pending = manager.list_pending()
        assert len(pending) == 1
        assert pending[0].token.token_id == token.token_id
        assert pending[0].token.verdict == ApprovalVerdict.conditional
        assert pending[0].decision is None
        assert pending[0].requested_at.tzinfo is not None

    @pytest.mark.parametrize("verdict", [ApprovalVerdict.approved, ApprovalVerdict.declined])
    def test_resolve_approval_verdict_and_check(self, verdict: ApprovalVerdict) -> None:
        manager = LiveApprovalManager(timeout=timedelta(minutes=10))
        token = manager.request_approval(_work_item("wi-resolve"), ApprovalScope.full_plan)

        first = manager.resolve(token.token_id, verdict, resolved_by="owner")
        second = manager.resolve(token.token_id, ApprovalVerdict.declined, resolved_by="owner")

        assert first.verdict == verdict
        assert second.verdict == verdict  # idempotent once resolved
        checked = manager.check_approval(token.token_id)
        assert checked is not None
        assert checked.verdict == verdict
        assert manager.list_pending() == []

    def test_timeout_prunes_expired_pending_approval(self) -> None:
        # Use a tiny positive timeout so the token is valid at creation but expires almost instantly
        manager = LiveApprovalManager(timeout=timedelta(milliseconds=1))
        token = manager.request_approval(_work_item("wi-expired"), ApprovalScope.full_plan)

        import time
        time.sleep(0.01)  # ensure expiry

        assert token.expires_at <= _utc_now()
        # After expiry, check_approval and list_pending should prune
        assert manager.check_approval(token.token_id) is None
        assert manager.list_pending() == []
        with pytest.raises(KeyError, match="unknown approval token"):
            manager.resolve(token.token_id, ApprovalVerdict.approved, resolved_by="owner")

    def test_plan_hash_binding_uses_work_item_canonical_hash(self) -> None:
        manager = LiveApprovalManager(timeout=timedelta(minutes=10))
        work_item = _work_item("wi-plan-hash", body="initial body")

        token = manager.request_approval(work_item, ApprovalScope.full_plan)

        assert token.work_item_id == work_item.id
        assert token.plan_hash == work_item.plan_hash()

        work_item.status = WorkItemStatus.running
        work_item.attempts = 3
        assert token.plan_hash == token.plan_hash

        changed_plan = work_item.model_copy(update={"body": "changed body"})
        assert token.plan_hash != changed_plan.plan_hash()


# ---------------------------------------------------------------------------
# 2) ApprovalToken model — spec Section 3.6
# ---------------------------------------------------------------------------


class TestApprovalTokenSecurityModel:
    def test_base64bytes_json_round_trip(self) -> None:
        token = _approval_token(signature=b"\x00\xffbinary-data")

        encoded_json = token.model_dump_json()
        loaded = ApprovalToken.model_validate_json(encoded_json)

        assert loaded.signature == b"\x00\xffbinary-data"

    def test_invalid_base64_signature_rejected(self) -> None:
        now = _utc_now()
        payload = {
            "token_id": "token-invalid",
            "plan_hash": "abc",
            "work_item_id": "wi",
            "scope": ApprovalScope.full_plan,
            "verdict": ApprovalVerdict.approved,
            "signature": "***not-base64***",
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=1)).isoformat(),
            "nonce": "n1",
        }

        with pytest.raises(ValidationError, match="invalid base64 data"):
            ApprovalToken.model_validate(payload)

    def test_expiry_must_be_after_issued_at(self) -> None:
        now = _utc_now()
        with pytest.raises(ValidationError, match="expires_at must be greater"):
            _approval_token(issued_at=now, expires_at=now)

    def test_standing_scope_requires_spawn_policy_hash(self) -> None:
        with pytest.raises(ValidationError, match="spawn_policy_hash"):
            _approval_token(
                scope=ApprovalScope.standing,
                conditions={},
            )

    def test_spawn_policy_hash_canonicalization_equivalence(self) -> None:
        hash_a = _spawn_policy_hash(
            failure_context_template="Fix $failed_checks and rerun",
            skills=["security", "security", "notify"],
            gates=[
                {"name": "risk", "threshold": "high"},
                {"threshold": "low", "name": "format"},
            ],
            verify=[
                {"name": "pytest", "run": "pytest -q"},
                {"run": "ruff check", "name": "ruff"},
            ],
            escalation_config={"report": {"action": "report"}, "retry": {"max_retries": 1}},
        )
        hash_b = _spawn_policy_hash(
            failure_context_template="Fix $failed_checks and rerun",
            skills=["notify", "security"],
            gates=[
                {"threshold": "low", "name": "format"},
                {"name": "risk", "threshold": "high"},
            ],
            verify=[
                {"run": "ruff check", "name": "ruff"},
                {"name": "pytest", "run": "pytest -q"},
            ],
            escalation_config={"retry": {"max_retries": 1}, "report": {"action": "report"}},
        )

        assert hash_a == hash_b

        standing = _approval_token(
            scope=ApprovalScope.standing,
            max_executions=10,
            conditions={"spawn_policy_hash": hash_a},
        )
        assert standing.conditions["spawn_policy_hash"] == hash_b
        assert standing.max_executions == 10


# ---------------------------------------------------------------------------
# 3) Nonce Store — SQLiteNonceStore
# ---------------------------------------------------------------------------


@pytest.fixture
async def nonce_db_path(tmp_path: Path) -> str:
    db_path = tmp_path / "security_nonces.db"
    await run_migrations(str(db_path))
    return str(db_path)


@pytest.fixture
async def nonce_store(nonce_db_path: str) -> SQLiteNonceStore:
    return SQLiteNonceStore(nonce_db_path)


@pytest.mark.asyncio
async def test_nonce_store_record_and_check(nonce_store: SQLiteNonceStore) -> None:
    assert await nonce_store.is_used("exec", "nonce-1") is False
    await nonce_store.record("exec", "nonce-1")
    assert await nonce_store.is_used("exec", "nonce-1") is True


@pytest.mark.asyncio
async def test_nonce_store_replay_detection_is_idempotent(
    nonce_store: SQLiteNonceStore,
    nonce_db_path: str,
) -> None:
    await nonce_store.record("exec", "replay-nonce")
    await nonce_store.record("exec", "replay-nonce")

    assert await nonce_store.is_used("exec", "replay-nonce") is True
    async with aiosqlite.connect(nonce_db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM nonces WHERE domain = ? AND nonce = ?",
            ("exec", "replay-nonce"),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1


@pytest.mark.asyncio
async def test_nonce_store_ttl_expiry_prunes_old_entries(nonce_store: SQLiteNonceStore) -> None:
    await nonce_store.record("msg", "ttl-nonce")

    pruned_too_early = await nonce_store.prune_expired(_utc_now() - timedelta(minutes=1))
    assert pruned_too_early == 0
    assert await nonce_store.is_used("msg", "ttl-nonce") is True

    pruned = await nonce_store.prune_expired(_utc_now() + timedelta(minutes=1))
    assert pruned == 1
    assert await nonce_store.is_used("msg", "ttl-nonce") is False


# ---------------------------------------------------------------------------
# 4) Message signing flow (spec 5.1 step 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason=(
        "Stream step 2 signature verification is not implemented yet; owner sender_id is "
        "currently trusted without cryptographic verification."
    )
)
async def test_unsigned_owner_message_taint_is_downgraded_to_external(
    channel,
    turn_context,
    context_manager,
) -> None:
    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id="owner",
        default_context_profile="conversation",
    )

    await stream._process_turn(_msg("unsigned owner message", sender_id="owner"))

    chronicle = context_manager.get_zone("owner", ContextZone.chronicle)
    assert chronicle[0].taint == TaintLevel.external


# ---------------------------------------------------------------------------
# 5) Standing approval verification behavior (spec 5.2.3 target behavior)
# ---------------------------------------------------------------------------


class TestStandingApprovalVerification:
    def test_standing_approval_allows_multiple_executions_until_max_uses(self) -> None:
        store = _CollectingWorkItemStore()
        manager = SilasGoalManager(goals_config=[_goal("goal-multi")], work_item_store=store)
        goal = manager.load_goals()[0]
        assert goal.spawn_policy_hash is not None

        manager.grant_standing_approval(
            goal_id=goal.goal_id,
            policy_hash=goal.spawn_policy_hash,
            granted_by="owner",
            expires_at=None,
            max_uses=2,
        )

        manager.run_goal(goal.goal_id)
        manager.run_goal(goal.goal_id)
        manager.run_goal(goal.goal_id)

        assert [item.needs_approval for item in store.saved] == [False, False, True]

    def test_spawn_policy_hash_binding_rejects_mismatched_hash(self) -> None:
        store = _CollectingWorkItemStore()
        manager = SilasGoalManager(goals_config=[_goal("goal-bind")], work_item_store=store)
        goal = manager.load_goals()[0]
        assert goal.spawn_policy_hash is not None

        wrong_hash = hashlib.sha256(b"not-the-goal-policy").hexdigest()
        manager.grant_standing_approval(
            goal_id=goal.goal_id,
            policy_hash=wrong_hash,
            granted_by="owner",
            expires_at=None,
            max_uses=5,
        )

        manager.run_goal(goal.goal_id)
        assert len(store.saved) == 1
        assert store.saved[0].needs_approval is True

    def test_spawn_policy_hash_canonicalized_hex_allows_case_insensitive_binding(self) -> None:
        store = _CollectingWorkItemStore()
        manager = SilasGoalManager(
            goals_config=[_goal("goal-case-canonicalization")],
            work_item_store=store,
        )
        goal = manager.load_goals()[0]
        assert goal.spawn_policy_hash is not None

        manager.grant_standing_approval(
            goal_id=goal.goal_id,
            policy_hash=goal.spawn_policy_hash.upper(),
            granted_by="owner",
            expires_at=None,
            max_uses=1,
        )

        manager.run_goal(goal.goal_id)
        assert len(store.saved) == 1
        assert store.saved[0].needs_approval is False
