from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from silas.models.approval import (
    ApprovalDecision,
    ApprovalScope,
    ApprovalToken,
    ApprovalVerdict,
    PendingApproval,
)
from silas.models.work import WorkItem


@runtime_checkable
class ApprovalVerifier(Protocol):
    async def issue_token(
        self,
        work_item: WorkItem,
        decision: ApprovalDecision,
        scope: ApprovalScope = ApprovalScope.full_plan,
    ) -> ApprovalToken: ...

    async def verify(
        self,
        token: ApprovalToken,
        work_item: WorkItem,
        spawned_task: WorkItem | None = None,
    ) -> tuple[bool, str]: ...

    async def check(self, token: ApprovalToken, work_item: WorkItem) -> tuple[bool, str]: ...


@runtime_checkable
class NonceStore(Protocol):
    async def is_used(self, domain: str, nonce: str) -> bool: ...

    async def record(self, domain: str, nonce: str) -> None: ...

    async def prune_expired(self, older_than: datetime) -> int: ...


@runtime_checkable
class ApprovalManager(Protocol):
    def request_approval(self, work_item: WorkItem, scope: ApprovalScope) -> ApprovalToken: ...

    def check_approval(self, token_id: str) -> ApprovalDecision | None: ...

    def resolve(
        self,
        token_id: str,
        verdict: ApprovalVerdict,
        resolved_by: str,
    ) -> ApprovalDecision: ...

    def list_pending(self) -> list[PendingApproval]: ...


__all__ = ["ApprovalVerifier", "NonceStore", "ApprovalManager"]
