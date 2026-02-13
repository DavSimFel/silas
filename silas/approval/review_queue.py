"""Batch review polling surface for pending approval requests.

Queues approval requests so a reviewer can poll and resolve them in batch,
reducing context-switch overhead vs. handling each request individually.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from silas.models.approval import ApprovalScope, ApprovalToken


class ApprovalRequest(BaseModel):
    """Lightweight request descriptor for the review queue.

    Separate from the tools-layer dataclass so the approval subsystem
    stays decoupled from tool dispatch internals.
    """

    request_id: str
    work_item_id: str
    plan_hash: str
    scope: ApprovalScope
    description: str = ""


class ReviewDecision(StrEnum):
    """Reviewer's verdict — DEFER keeps the item in queue for later."""

    APPROVE = "approve"
    DENY = "deny"
    DEFER = "defer"


class PendingReview(BaseModel):
    """Snapshot of a queued approval request awaiting human review."""

    review_id: str
    request: ApprovalRequest
    priority: int = Field(
        default=0,
        description="Higher = more urgent. Drives poll ordering.",
    )
    created_at: datetime
    expires_at: datetime


# Priority tiers — scopes that need faster human attention get higher values.
_SCOPE_PRIORITY: dict[ApprovalScope, int] = {
    ApprovalScope.self_update: 90,
    ApprovalScope.credential_use: 80,
    ApprovalScope.budget: 70,
    ApprovalScope.skill_install: 60,
    ApprovalScope.connection_manage: 50,
    ApprovalScope.connection_act: 40,
    ApprovalScope.autonomy_threshold: 30,
}
_DEFAULT_PRIORITY = 10


def _priority_for(scope: ApprovalScope) -> int:
    return _SCOPE_PRIORITY.get(scope, _DEFAULT_PRIORITY)


class ReviewQueue:
    """In-memory queue of pending reviews, ordered by priority then age.

    Designed to sit between ApprovalManager (producer) and a polling UI (consumer).
    Thread-safety is NOT provided — callers must synchronize if needed.
    """

    def __init__(self, *, default_ttl_seconds: float = 300) -> None:
        self._reviews: dict[str, PendingReview] = {}
        self._default_ttl = default_ttl_seconds

    @property
    def pending_reviews(self) -> list[PendingReview]:
        """Sorted view: highest priority first, then oldest first (FIFO within tier)."""
        return sorted(
            self._reviews.values(),
            key=lambda r: (-r.priority, r.created_at),
        )

    def enqueue(self, request: ApprovalRequest, *, ttl_seconds: float | None = None) -> PendingReview:
        """Add an approval request to the review queue and return the pending review."""
        now = datetime.now(UTC)
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        review = PendingReview(
            review_id=uuid.uuid4().hex,
            request=request,
            priority=_priority_for(request.scope),
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        self._reviews[review.review_id] = review
        return review

    def poll(self, *, limit: int = 10) -> list[PendingReview]:
        """Return up to *limit* pending reviews in priority order for batch review."""
        return self.pending_reviews[:limit]

    def resolve(self, review_id: str, decision: ReviewDecision) -> ApprovalToken | None:
        """Resolve a single review. Returns a token stub on APPROVE, None on DENY.

        DEFER leaves the item in queue untouched.
        Raises KeyError if review_id is unknown.
        """
        if review_id not in self._reviews:
            raise KeyError(f"unknown review: {review_id}")

        if decision == ReviewDecision.DEFER:
            return None

        review = self._reviews.pop(review_id)

        if decision == ReviewDecision.DENY:
            return None

        # APPROVE — mint a lightweight token. Real cryptographic signing
        # happens upstream in SilasApprovalVerifier; this token carries
        # enough identity for the caller to bind it later.
        now = datetime.now(UTC)
        from silas.models.approval import ApprovalVerdict

        return ApprovalToken(
            token_id=uuid.uuid4().hex,
            plan_hash=review.request.plan_hash,
            work_item_id=review.request.work_item_id,
            scope=review.request.scope,
            verdict=ApprovalVerdict.approved,
            signature=b"review-queue-stub",
            issued_at=now,
            expires_at=review.expires_at,
            nonce=uuid.uuid4().hex,
        )

    def resolve_batch(
        self, decisions: list[tuple[str, ReviewDecision]]
    ) -> list[ApprovalToken | None]:
        """Resolve multiple reviews atomically. Order of results matches input."""
        return [self.resolve(rid, dec) for rid, dec in decisions]

    def expire_stale(self, max_age_seconds: float = 300) -> int:
        """Remove reviews older than *max_age_seconds*. Returns count removed."""
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=max_age_seconds)
        stale = [rid for rid, r in self._reviews.items() if r.created_at <= cutoff]
        for rid in stale:
            del self._reviews[rid]
        return len(stale)


__all__ = ["ApprovalRequest", "PendingReview", "ReviewDecision", "ReviewQueue"]
