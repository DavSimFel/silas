from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from silas.models.approval import (
    ApprovalDecision,
    ApprovalScope,
    ApprovalToken,
    ApprovalVerdict,
    PendingApproval,
)
from silas.models.work import WorkItem


class LiveApprovalManager:
    def __init__(self, timeout: timedelta = timedelta(hours=1)) -> None:
        self._timeout = timeout
        self._pending: dict[str, PendingApproval] = {}

    def request_approval(self, work_item: WorkItem, scope: ApprovalScope) -> ApprovalToken:
        self._prune_expired()
        now = datetime.now(timezone.utc)
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
        )
        self._pending[token.token_id] = PendingApproval(
            token=token,
            requested_at=now,
        )
        return token

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

        decision = ApprovalDecision(verdict=verdict)
        self._pending[token_id] = pending.model_copy(
            update={
                "decision": decision,
                "resolved_at": datetime.now(timezone.utc),
                "resolved_by": resolved_by,
            }
        )
        return decision

    def list_pending(self) -> list[PendingApproval]:
        self._prune_expired()
        return [item for item in self._pending.values() if item.decision is None]

    def _prune_expired(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [
            token_id
            for token_id, item in self._pending.items()
            if item.token.expires_at <= now
        ]
        for token_id in expired:
            self._pending.pop(token_id, None)


__all__ = ["LiveApprovalManager"]
