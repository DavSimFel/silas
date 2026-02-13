"""Tests for the batch review polling surface (§0.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from silas.approval.review_queue import (
    ApprovalRequest,
    PendingReview,
    ReviewDecision,
    ReviewQueue,
)
from silas.models.approval import ApprovalScope, ApprovalToken

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(
    scope: ApprovalScope = ApprovalScope.full_plan,
    *,
    request_id: str = "req-1",
    work_item_id: str = "wi-1",
    plan_hash: str = "abc123",
) -> ApprovalRequest:
    return ApprovalRequest(
        request_id=request_id,
        work_item_id=work_item_id,
        plan_hash=plan_hash,
        scope=scope,
    )


# ---------------------------------------------------------------------------
# Enqueue + poll ordering
# ---------------------------------------------------------------------------

class TestEnqueueAndPoll:
    def test_enqueue_returns_pending_review(self) -> None:
        q = ReviewQueue()
        review = q.enqueue(_make_request())
        assert isinstance(review, PendingReview)
        assert review.request.request_id == "req-1"

    def test_poll_returns_priority_order(self) -> None:
        """Higher-priority scopes should appear first in poll results."""
        q = ReviewQueue()
        # low-priority first
        q.enqueue(_make_request(ApprovalScope.full_plan, request_id="low"))
        # high-priority second
        q.enqueue(_make_request(ApprovalScope.self_update, request_id="high"))

        batch = q.poll(limit=10)
        assert len(batch) == 2
        # self_update (90) should come before full_plan (10)
        assert batch[0].request.request_id == "high"
        assert batch[1].request.request_id == "low"

    def test_poll_fifo_within_same_priority(self) -> None:
        """Items at the same priority tier should be FIFO (oldest first)."""
        q = ReviewQueue()
        q.enqueue(_make_request(ApprovalScope.full_plan, request_id="first"))
        q.enqueue(_make_request(ApprovalScope.full_plan, request_id="second"))

        batch = q.poll()
        assert batch[0].request.request_id == "first"
        assert batch[1].request.request_id == "second"

    def test_poll_respects_limit(self) -> None:
        q = ReviewQueue()
        for i in range(5):
            q.enqueue(_make_request(request_id=f"r-{i}"))
        assert len(q.poll(limit=3)) == 3

    def test_poll_empty_queue(self) -> None:
        q = ReviewQueue()
        assert q.poll() == []


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------

class TestResolve:
    def test_approve_returns_token(self) -> None:
        q = ReviewQueue()
        review = q.enqueue(_make_request())
        token = q.resolve(review.review_id, ReviewDecision.APPROVE)
        assert isinstance(token, ApprovalToken)
        assert token.work_item_id == "wi-1"
        assert token.plan_hash == "abc123"

    def test_deny_returns_none(self) -> None:
        q = ReviewQueue()
        review = q.enqueue(_make_request())
        result = q.resolve(review.review_id, ReviewDecision.DENY)
        assert result is None
        # Should be removed from queue
        assert len(q.poll()) == 0

    def test_defer_keeps_in_queue(self) -> None:
        """DEFER means 'not now' — item stays for the next poll cycle."""
        q = ReviewQueue()
        review = q.enqueue(_make_request())
        result = q.resolve(review.review_id, ReviewDecision.DEFER)
        assert result is None
        # Still in queue
        assert len(q.poll()) == 1

    def test_resolve_unknown_raises(self) -> None:
        q = ReviewQueue()
        with pytest.raises(KeyError, match="unknown review"):
            q.resolve("nonexistent", ReviewDecision.APPROVE)

    def test_approve_removes_from_queue(self) -> None:
        q = ReviewQueue()
        review = q.enqueue(_make_request())
        q.resolve(review.review_id, ReviewDecision.APPROVE)
        assert len(q.poll()) == 0


# ---------------------------------------------------------------------------
# Batch resolve
# ---------------------------------------------------------------------------

class TestBatchResolve:
    def test_mixed_decisions(self) -> None:
        q = ReviewQueue()
        r1 = q.enqueue(_make_request(request_id="a"))
        r2 = q.enqueue(_make_request(request_id="b"))
        r3 = q.enqueue(_make_request(request_id="c"))

        results = q.resolve_batch([
            (r1.review_id, ReviewDecision.APPROVE),
            (r2.review_id, ReviewDecision.DENY),
            (r3.review_id, ReviewDecision.DEFER),
        ])

        assert isinstance(results[0], ApprovalToken)
        assert results[1] is None
        assert results[2] is None
        # Only deferred item remains
        assert len(q.poll()) == 1


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

class TestExpireStale:
    def test_expire_removes_old_entries(self) -> None:
        q = ReviewQueue()
        review = q.enqueue(_make_request())
        # Backdate the created_at so it's stale
        q._reviews[review.review_id] = review.model_copy(
            update={"created_at": datetime.now(UTC) - timedelta(seconds=600)}
        )
        removed = q.expire_stale(max_age_seconds=300)
        assert removed == 1
        assert len(q.poll()) == 0

    def test_expire_keeps_fresh_entries(self) -> None:
        q = ReviewQueue()
        q.enqueue(_make_request())
        removed = q.expire_stale(max_age_seconds=300)
        assert removed == 0
        assert len(q.poll()) == 1


# ---------------------------------------------------------------------------
# Integration: LiveApprovalManager enqueues into review queue
# ---------------------------------------------------------------------------

class TestManagerIntegration:
    def test_request_approval_enqueues(self) -> None:
        """Non-auto-approved requests should appear in the review queue."""
        from silas.approval.manager import LiveApprovalManager
        from silas.models.work import WorkItem

        mgr = LiveApprovalManager()
        # Build a minimal work item — need plan_hash() and .id
        wi = WorkItem(
            id="test-wi",
            title="test",
            type="task",
            status="pending",
            body="do the thing",
            steps=[],
        )
        mgr.request_approval(wi, ApprovalScope.full_plan)
        queue = mgr.get_review_queue()
        assert isinstance(queue, ReviewQueue)

    def test_high_risk_scope_enqueues(self) -> None:
        """self_update scope should never auto-approve → always enqueued."""
        from silas.approval.manager import LiveApprovalManager
        from silas.models.work import WorkItem

        mgr = LiveApprovalManager()
        wi = WorkItem(
            id="test-wi-2",
            title="test",
            type="task",
            status="pending",
            body="do the thing",
            steps=[],
        )
        mgr.request_approval(wi, ApprovalScope.self_update)
        pending = mgr.get_review_queue().poll()
        assert len(pending) == 1
        assert pending[0].request.scope == ApprovalScope.self_update
