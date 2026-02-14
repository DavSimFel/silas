from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from silas.approval.fatigue import (
    ApprovalFatigueMitigator,
    DecisionRecord,
    FatigueAnalysis,
)
from silas.approval.review_queue import ApprovalRequest, ReviewQueue
from silas.models.approval import (
    ApprovalDecision,
    ApprovalScope,
    ApprovalToken,
    ApprovalVerdict,
    PendingApproval,
)
from silas.models.work import WorkItem
from silas.proactivity.ux_metrics import UXMetricsCollector


class LiveApprovalManager:
    def __init__(
        self,
        timeout: timedelta = timedelta(hours=1),
        ux_metrics: UXMetricsCollector | None = None,
        goal_manager: object | None = None,
    ) -> None:
        self._timeout = timeout
        self._pending: dict[str, PendingApproval] = {}
        # Optional — callers that don't care about metrics can omit it.
        self._ux_metrics = ux_metrics
        self._fatigue = ApprovalFatigueMitigator()
        # Resolved decisions for fatigue tracking
        self._decision_log: list[DecisionRecord] = []
        # Batch review surface — polls draw from this queue.
        self._review_queue = ReviewQueue()
        # Optional dependency used for standing approval lookups on spawned work.
        self._goal_manager = goal_manager

    def get_review_queue(self) -> ReviewQueue:
        """Accessor so external consumers can poll/resolve pending reviews."""
        return self._review_queue

    def bind_goal_manager(self, goal_manager: object | None) -> None:
        """Attach goal manager dependency used for standing approval lookups."""
        self._goal_manager = goal_manager

    def get_fatigue_analysis(self, *, window_minutes: int = 30) -> FatigueAnalysis:
        """Snapshot of current fatigue state for the approval queue."""
        return self._fatigue.analyze_fatigue(self._decision_log, window_minutes=window_minutes)

    def request_approval(self, work_item: WorkItem, scope: ApprovalScope) -> ApprovalToken:
        if work_item.spawned_by is not None:
            standing_token = self.check_standing_approval(work_item, self._goal_manager)
            if standing_token is not None:
                now = datetime.now(UTC)
                decision = ApprovalDecision(verdict=ApprovalVerdict.approved)
                self._pending[standing_token.token_id] = PendingApproval(
                    token=standing_token,
                    requested_at=now,
                    decision=decision,
                    resolved_at=now,
                    resolved_by="standing_auto",
                )
                return standing_token

        self._prune_expired()
        analysis = self.get_fatigue_analysis()

        # High fatigue: auto-approve low-risk scopes so the human
        # only sees decisions that actually need attention.
        if self._fatigue.should_auto_approve(analysis, scope):
            now = datetime.now(UTC)
            token = ApprovalToken(
                token_id=uuid.uuid4().hex,
                plan_hash=work_item.plan_hash(),
                work_item_id=work_item.id,
                scope=scope,
                verdict=ApprovalVerdict.approved,
                signature=b"auto-fatigue",
                issued_at=now,
                expires_at=now + self._timeout,
                nonce=uuid.uuid4().hex,
            )
            decision = ApprovalDecision(verdict=ApprovalVerdict.approved)
            self._pending[token.token_id] = PendingApproval(
                token=token,
                requested_at=now,
                decision=decision,
                resolved_at=now,
                resolved_by="fatigue_auto",
            )
            return token

        now = datetime.now(UTC)
        token = ApprovalToken(
            token_id=uuid.uuid4().hex,
            plan_hash=work_item.plan_hash(),
            work_item_id=work_item.id,
            scope=scope,
            verdict=ApprovalVerdict.conditional,
            signature=b"pending",
            issued_at=now,
            expires_at=now + self._timeout,
            nonce=uuid.uuid4().hex,
            conditions={"fatigue_level": analysis.fatigue_level.value},
        )
        self._pending[token.token_id] = PendingApproval(
            token=token,
            requested_at=now,
        )

        # Feed the review queue so batch polling surfaces pick it up.
        self._review_queue.enqueue(
            ApprovalRequest(
                request_id=token.token_id,
                work_item_id=work_item.id,
                plan_hash=work_item.plan_hash(),
                scope=scope,
            )
        )
        return token

    def check_standing_approval(
        self,
        work_item: WorkItem,
        goal_manager: object,
    ) -> ApprovalToken | None:
        """Resolve a standing token for goal-spawned work when still active."""
        goal_id = work_item.spawned_by
        if goal_id is None:
            return None

        get_standing = getattr(goal_manager, "get_standing_approval", None)
        if not callable(get_standing):
            return None

        try:
            approval = get_standing(goal_id, work_item.plan_hash())
        except (TypeError, ValueError, RuntimeError):
            return None
        if approval is None:
            return None

        return self._extract_active_standing_token(approval)

    def _extract_active_standing_token(self, approval: object) -> ApprovalToken | None:
        """Normalize untyped goal-manager payloads and ensure token usability."""
        token = getattr(approval, "approval_token", None)
        if not isinstance(token, ApprovalToken):
            return None

        now = datetime.now(UTC)
        expires_at = getattr(approval, "expires_at", None)
        if isinstance(expires_at, datetime) and expires_at <= now:
            return None

        uses_remaining = getattr(approval, "uses_remaining", None)
        if isinstance(uses_remaining, int) and uses_remaining <= 0:
            return None

        if token.expires_at <= now:
            return None
        if token.executions_used >= token.max_executions:
            return None
        if token.scope != ApprovalScope.standing:
            return None
        return token.model_copy(deep=True)

    def check_approval(self, token_id: str) -> ApprovalDecision | None:
        self._prune_expired()
        pending = self._pending.get(token_id)
        if pending is None:
            return None
        return pending.decision

    def resolve(
        self,
        token_id: str,
        verdict: ApprovalVerdict,
        resolved_by: str,
    ) -> ApprovalDecision:
        self._prune_expired()
        pending = self._pending.get(token_id)
        if pending is None:
            raise KeyError(f"unknown approval token: {token_id}")
        if pending.decision is not None:
            return pending.decision

        now = datetime.now(UTC)
        decision = ApprovalDecision(verdict=verdict)
        self._pending[token_id] = pending.model_copy(
            update={
                "decision": decision,
                "resolved_at": now,
                "resolved_by": resolved_by,
            }
        )

        # Record UX timing so fatigue/throughput metrics stay current.
        if self._ux_metrics is not None:
            duration_ms = int((now - pending.requested_at).total_seconds() * 1000)
            self._ux_metrics.record_approval_decision(
                token_id=token_id,
                decision=verdict.value,
                duration_ms=duration_ms,
            )

        # Feed timing data back to fatigue tracker
        elapsed_ms = (now - pending.requested_at).total_seconds() * 1000
        self._decision_log.append(
            DecisionRecord(
                decided_at=now,
                decision_time_ms=elapsed_ms,
                scope=pending.token.scope,
            )
        )
        return decision

    def list_pending(self) -> list[PendingApproval]:
        self._prune_expired()
        return [item for item in self._pending.values() if item.decision is None]

    def _prune_expired(self) -> None:
        now = datetime.now(UTC)
        expired = [
            token_id for token_id, item in self._pending.items() if item.token.expires_at <= now
        ]
        for token_id in expired:
            self._pending.pop(token_id, None)


__all__ = ["LiveApprovalManager"]
