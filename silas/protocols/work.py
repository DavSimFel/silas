from __future__ import annotations

from typing import Protocol, runtime_checkable

from silas.models.execution import VerificationReport
from silas.models.work import (
    BudgetUsed,
    VerificationCheck,
    WorkItem,
    WorkItemResult,
    WorkItemStatus,
)


@runtime_checkable
class WorkItemExecutor(Protocol):
    async def execute(self, item: WorkItem) -> WorkItemResult: ...


@runtime_checkable
class VerificationRunner(Protocol):
    async def run_checks(self, checks: list[VerificationCheck]) -> VerificationReport: ...


@runtime_checkable
class WorkItemStore(Protocol):
    async def save(self, item: WorkItem) -> None: ...

    async def get(self, work_item_id: str) -> WorkItem | None: ...

    async def list_by_status(self, status: WorkItemStatus) -> list[WorkItem]: ...

    async def list_by_parent(self, parent_id: str) -> list[WorkItem]: ...

    async def update_status(
        self,
        work_item_id: str,
        status: WorkItemStatus,
        budget_used: BudgetUsed,
    ) -> None: ...


@runtime_checkable
class PlanParser(Protocol):
    def parse(self, markdown: str) -> WorkItem: ...


__all__ = ["PlanParser", "VerificationRunner", "WorkItemExecutor", "WorkItemStore"]
