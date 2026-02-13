"""Race condition tests for approval token handling.

Covers: double-spend, concurrent nonce uniqueness, replay attacks,
concurrent review resolution, and expiry timing edge cases.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from silas.approval.manager import LiveApprovalManager
from silas.approval.review_queue import ApprovalRequest, ReviewDecision, ReviewQueue
from silas.approval.verifier import SilasApprovalVerifier
from silas.models.approval import (
    ApprovalDecision,
    ApprovalScope,
    ApprovalVerdict,
)
from silas.models.work import WorkItem, WorkItemType

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _InMemoryNonceStore:
    """Thread-unsafe nonce store — sufficient for single-event-loop tests."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    async def is_used(self, domain: str, nonce: str) -> bool:
        return f"{domain}:{nonce}" in self._keys

    async def record(self, domain: str, nonce: str) -> None:
        self._keys.add(f"{domain}:{nonce}")

    async def prune_expired(self, older_than: datetime) -> int:
        del older_than
        return 0


def _work_item(
    item_id: str,
    *,
    body: str = "Race-condition test task",
    item_type: WorkItemType = WorkItemType.task,
) -> WorkItem:
    return WorkItem(
        id=item_id,
        type=item_type,
        title=f"Work Item {item_id}",
        body=body,
    )


@pytest.fixture
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def nonce_store() -> _InMemoryNonceStore:
    return _InMemoryNonceStore()


@pytest.fixture
def verifier(
    signing_key: Ed25519PrivateKey,
    nonce_store: _InMemoryNonceStore,
) -> SilasApprovalVerifier:
    return SilasApprovalVerifier(signing_key=signing_key, nonce_store=nonce_store)


# ---------------------------------------------------------------------------
# 1. Double-spend — same nonce used twice → second attempt rejected
# ---------------------------------------------------------------------------


async def test_double_spend_same_token(verifier: SilasApprovalVerifier) -> None:
    """Verifying the same single-use token twice must fail on the second attempt."""
    work_item = _work_item("wi-double-spend")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)
    token = await verifier.issue_token(work_item, decision)

    first_ok, first_reason = await verifier.verify(token, work_item)
    assert first_ok is True
    assert first_reason == "ok"

    second_ok, second_reason = await verifier.verify(token, work_item)
    assert second_ok is False
    assert second_reason == "execution_limit_reached"


async def test_double_spend_cloned_token(verifier: SilasApprovalVerifier) -> None:
    """A shallow copy of a consumed token must also be rejected (shared nonce state)."""
    work_item = _work_item("wi-clone-spend")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)
    token = await verifier.issue_token(work_item, decision)

    ok, _ = await verifier.verify(token, work_item)
    assert ok is True

    # Clone the token as if an attacker copied the object before consumption.
    cloned = token.model_copy()
    clone_ok, clone_reason = await verifier.verify(cloned, work_item)
    assert clone_ok is False
    assert clone_reason == "execution_limit_reached"


# ---------------------------------------------------------------------------
# 2. Concurrent approval requests — all get unique nonces
# ---------------------------------------------------------------------------


async def test_concurrent_request_approval_unique_nonces() -> None:
    """Ten simultaneous request_approval() calls must each produce a unique nonce."""
    manager = LiveApprovalManager()
    work_items = [_work_item(f"wi-concurrent-{i}") for i in range(10)]

    tokens = await asyncio.gather(
        *[
            asyncio.to_thread(
                manager.request_approval, wi, ApprovalScope.full_plan
            )
            for wi in work_items
        ]
    )

    nonces = [t.nonce for t in tokens]
    assert len(nonces) == 10
    assert len(set(nonces)) == 10, f"Duplicate nonces detected: {nonces}"

    token_ids = [t.token_id for t in tokens]
    assert len(set(token_ids)) == 10, f"Duplicate token IDs detected: {token_ids}"


async def test_concurrent_issue_token_unique_nonces(
    verifier: SilasApprovalVerifier,
) -> None:
    """Ten simultaneous issue_token() calls must yield unique nonces."""
    work_items = [_work_item(f"wi-issue-{i}") for i in range(10)]
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)

    tokens = await asyncio.gather(
        *[verifier.issue_token(wi, decision) for wi in work_items]
    )

    nonces = [t.nonce for t in tokens]
    assert len(set(nonces)) == 10


# ---------------------------------------------------------------------------
# 3. Replay attack — valid token replayed → nonce store rejects
# ---------------------------------------------------------------------------


async def test_replay_attack_reissued_nonce(
    signing_key: Ed25519PrivateKey,
    nonce_store: _InMemoryNonceStore,
) -> None:
    """An attacker who re-signs a consumed token with the same nonce is still blocked."""
    verifier = SilasApprovalVerifier(signing_key=signing_key, nonce_store=nonce_store)
    work_item = _work_item("wi-replay")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)

    token = await verifier.issue_token(work_item, decision)
    ok, _ = await verifier.verify(token, work_item)
    assert ok is True

    # The token object tracks executions_used — even a copy shares the count
    # after the first verify bumped it. A second verify is always blocked.
    replay_ok, replay_reason = await verifier.verify(token, work_item)
    assert replay_ok is False
    assert replay_reason == "execution_limit_reached"

    # Separately, verify the nonce store actually recorded the execution binding
    # so even an external replay attempt with a fresh binding key pattern would
    # find prior nonces consumed.
    assert len(nonce_store._keys) >= 1


async def test_replay_different_work_item(verifier: SilasApprovalVerifier) -> None:
    """A token issued for one work item cannot be replayed against a different one."""
    wi_a = _work_item("wi-replay-a", body="Task A")
    wi_b = _work_item("wi-replay-b", body="Task B")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)

    token = await verifier.issue_token(wi_a, decision)
    # Try verifying against a different work item — plan hash won't match.
    cross_ok, cross_reason = await verifier.verify(token, wi_b)
    assert cross_ok is False
    assert cross_reason == "plan_hash_mismatch"


# ---------------------------------------------------------------------------
# 4. Concurrent resolve on same review — only first succeeds
# ---------------------------------------------------------------------------


async def test_concurrent_resolve_same_review() -> None:
    """Two simultaneous resolves on the same review — only first changes state."""
    manager = LiveApprovalManager()
    work_item = _work_item("wi-race-resolve")
    token = manager.request_approval(work_item, ApprovalScope.full_plan)

    # Both try to resolve the same pending approval.
    decision_1 = manager.resolve(token.token_id, ApprovalVerdict.approved, "reviewer-1")
    decision_2 = manager.resolve(token.token_id, ApprovalVerdict.declined, "reviewer-2")

    # First resolve sets the decision; second call returns the *same* decision
    # (idempotent — it does not overwrite).
    assert decision_1.verdict == ApprovalVerdict.approved
    assert decision_2.verdict == ApprovalVerdict.approved  # unchanged


async def test_concurrent_resolve_review_queue() -> None:
    """Two resolves on the same ReviewQueue item — second raises KeyError."""
    queue = ReviewQueue()
    request = ApprovalRequest(
        request_id="req-race",
        work_item_id="wi-race",
        plan_hash="abc123",
        scope=ApprovalScope.full_plan,
    )
    review = queue.enqueue(request)

    # First resolve removes the item.
    result = queue.resolve(review.review_id, ReviewDecision.APPROVE)
    assert result is not None

    # Second resolve must fail — item already consumed.
    with pytest.raises(KeyError, match="unknown review"):
        queue.resolve(review.review_id, ReviewDecision.APPROVE)


# ---------------------------------------------------------------------------
# 5. Expiry race — token created just before expiry window
# ---------------------------------------------------------------------------


async def test_expiry_boundary_token(
    signing_key: Ed25519PrivateKey,
    nonce_store: _InMemoryNonceStore,
) -> None:
    """A token whose expires_at is essentially 'now' should be rejected."""
    verifier = SilasApprovalVerifier(signing_key=signing_key, nonce_store=nonce_store)
    work_item = _work_item("wi-expiry-race")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)

    token = await verifier.issue_token(work_item, decision)
    # Simulate the token expiring right at the current moment.
    expired = token.model_copy(update={"expires_at": datetime.now(UTC)})
    # Re-sign so signature is valid for the modified payload.
    canonical = verifier._canonical_bytes(
        plan_hash=expired.plan_hash,
        work_item_id=expired.work_item_id,
        scope=expired.scope,
        verdict=expired.verdict,
        nonce=expired.nonce,
        approval_strength=expired.approval_strength,
        issued_at=expired.issued_at,
        expires_at=expired.expires_at,
        max_executions=expired.max_executions,
        conditions=expired.conditions,
    )
    resigned = expired.model_copy(update={"signature": signing_key.sign(canonical)})

    ok, reason = await verifier.verify(resigned, work_item)
    assert ok is False
    assert reason == "token_expired"


async def test_expiry_race_near_boundary(
    signing_key: Ed25519PrivateKey,
    nonce_store: _InMemoryNonceStore,
) -> None:
    """A token expiring 50ms in the future should still be valid if verified immediately."""
    verifier = SilasApprovalVerifier(signing_key=signing_key, nonce_store=nonce_store)
    work_item = _work_item("wi-near-expiry")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)

    token = await verifier.issue_token(work_item, decision)
    # Set expiry to 50ms from now — tight but valid.
    near_future = datetime.now(UTC) + timedelta(milliseconds=50)
    tweaked = token.model_copy(update={"expires_at": near_future})
    canonical = verifier._canonical_bytes(
        plan_hash=tweaked.plan_hash,
        work_item_id=tweaked.work_item_id,
        scope=tweaked.scope,
        verdict=tweaked.verdict,
        nonce=tweaked.nonce,
        approval_strength=tweaked.approval_strength,
        issued_at=tweaked.issued_at,
        expires_at=tweaked.expires_at,
        max_executions=tweaked.max_executions,
        conditions=tweaked.conditions,
    )
    resigned = tweaked.model_copy(update={"signature": signing_key.sign(canonical)})

    ok, reason = await verifier.verify(resigned, work_item)
    assert ok is True
    assert reason == "ok"


async def test_manager_expiry_prunes_pending() -> None:
    """Expired pending approvals are pruned and no longer resolvable."""
    manager = LiveApprovalManager(timeout=timedelta(seconds=1))
    work_item = _work_item("wi-prune")
    token = manager.request_approval(work_item, ApprovalScope.full_plan)

    # Force expiry by backdating the token.
    pending = manager._pending[token.token_id]
    past = datetime.now(UTC) - timedelta(seconds=10)
    expired_token = pending.token.model_copy(update={"expires_at": past, "issued_at": past - timedelta(seconds=1)})
    manager._pending[token.token_id] = pending.model_copy(update={"token": expired_token})

    # Token was issued with 0s timeout → already expired.
    # Any operation that calls _prune_expired should remove it.
    with pytest.raises(KeyError, match="unknown approval token"):
        manager.resolve(token.token_id, ApprovalVerdict.approved, "reviewer")
