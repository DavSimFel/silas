from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from silas.approval.verifier import SilasApprovalVerifier
from silas.models.approval import ApprovalDecision, ApprovalScope, ApprovalToken, ApprovalVerdict
from silas.models.work import WorkItem, WorkItemType

pytestmark = pytest.mark.asyncio


class _InMemoryNonceStore:
    def __init__(self) -> None:
        self._keys: set[str] = set()

    async def is_used(self, domain: str, nonce: str) -> bool:
        return f"{domain}:{nonce}" in self._keys

    async def record(self, domain: str, nonce: str) -> None:
        self._keys.add(f"{domain}:{nonce}")

    async def prune_expired(self, older_than: datetime) -> int:
        del older_than
        return 0


@pytest.fixture
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def verifier(signing_key: Ed25519PrivateKey) -> SilasApprovalVerifier:
    return SilasApprovalVerifier(signing_key=signing_key, nonce_store=_InMemoryNonceStore())


def _work_item(
    item_id: str,
    *,
    body: str = "Run approval-protected task",
    parent: str | None = None,
    item_type: WorkItemType = WorkItemType.task,
) -> WorkItem:
    return WorkItem(
        id=item_id,
        type=item_type,
        title=f"Work Item {item_id}",
        body=body,
        parent=parent,
    )


def _token_canonical_bytes(token: ApprovalToken) -> bytes:
    payload: dict[str, object] = {
        "plan_hash": token.plan_hash,
        "work_item_id": token.work_item_id,
        "scope": token.scope.value,
        "verdict": token.verdict.value,
        "nonce": token.nonce,
        "approval_strength": token.approval_strength,
        "issued_at": token.issued_at.isoformat(),
        "expires_at": token.expires_at.isoformat(),
        "max_executions": token.max_executions,
        "conditions": token.conditions,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _resign(token: ApprovalToken, signing_key: Ed25519PrivateKey) -> ApprovalToken:
    signature: bytes = signing_key.sign(_token_canonical_bytes(token))
    return token.model_copy(update={"signature": signature})


async def test_issue_and_verify_roundtrip(verifier: SilasApprovalVerifier) -> None:
    work_item = _work_item("wi-roundtrip")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)

    token = await verifier.issue_token(work_item, decision)
    valid, reason = await verifier.verify(token, work_item)

    assert valid is True
    assert reason == "ok"
    assert token.plan_hash == work_item.plan_hash()
    assert token.executions_used == 1
    assert len(token.execution_nonces) == 1


async def test_verify_rejects_expired_token(
    verifier: SilasApprovalVerifier,
    signing_key: Ed25519PrivateKey,
) -> None:
    work_item = _work_item("wi-expired")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)
    token = await verifier.issue_token(work_item, decision)

    expired_token = token.model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(minutes=1)}
    )
    expired_token = _resign(expired_token, signing_key)

    valid, reason = await verifier.verify(expired_token, work_item)

    assert valid is False
    assert reason == "token_expired"


async def test_verify_rejects_tampered_plan_hash(verifier: SilasApprovalVerifier) -> None:
    work_item = _work_item("wi-planhash")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)
    token = await verifier.issue_token(work_item, decision)
    tampered_work_item = work_item.model_copy(update={"body": "Tampered plan body"})

    valid, reason = await verifier.verify(token, tampered_work_item)

    assert valid is False
    assert reason == "plan_hash_mismatch"


async def test_verify_consumes_execution_nonce(verifier: SilasApprovalVerifier) -> None:
    work_item = _work_item("wi-single-use")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)
    token = await verifier.issue_token(work_item, decision)

    first_valid, first_reason = await verifier.verify(token, work_item)
    second_valid, second_reason = await verifier.verify(token, work_item)

    assert first_valid is True
    assert first_reason == "ok"
    assert second_valid is False
    assert second_reason == "execution_limit_reached"
    assert token.executions_used == 1
    assert len(token.execution_nonces) == 1


async def test_check_does_not_consume_nonce(verifier: SilasApprovalVerifier) -> None:
    work_item = _work_item("wi-check")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)
    token = await verifier.issue_token(work_item, decision)

    valid_after_verify, reason_after_verify = await verifier.verify(token, work_item)
    assert valid_after_verify is True
    assert reason_after_verify == "ok"

    before_nonces = list(token.execution_nonces)
    before_used = token.executions_used

    check_valid, check_reason = await verifier.check(token, work_item)

    assert check_valid is True
    assert check_reason == "ok"
    assert token.execution_nonces == before_nonces
    assert token.executions_used == before_used


async def test_standing_token_multiple_executions(verifier: SilasApprovalVerifier) -> None:
    goal = _work_item("goal-standing", body="Monitor drift", item_type=WorkItemType.goal)
    decision = ApprovalDecision(
        verdict=ApprovalVerdict.approved,
        conditions={"spawn_policy_hash": "policy-v1", "max_executions": 3},
    )
    token = await verifier.issue_token(goal, decision, scope=ApprovalScope.standing)

    spawned_tasks = [
        _work_item("spawn-1", body="Fix check 1", parent=goal.id),
        _work_item("spawn-2", body="Fix check 2", parent=goal.id),
        _work_item("spawn-3", body="Fix check 3", parent=goal.id),
        _work_item("spawn-4", body="Fix check 4", parent=goal.id),
    ]

    for spawned_task in spawned_tasks[:3]:
        valid, reason = await verifier.verify(token, goal, spawned_task=spawned_task)
        assert valid is True
        assert reason == "ok"

    final_valid, final_reason = await verifier.verify(token, goal, spawned_task=spawned_tasks[3])

    assert final_valid is False
    assert final_reason == "execution_limit_reached"
    assert token.executions_used == 3
    assert len(token.execution_nonces) == 3
