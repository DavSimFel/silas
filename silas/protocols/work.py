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


@runtime_checkable
class ExecutorPool(Protocol):
    """Concurrency-capped pool that dispatches work items in parallel.

    Respects per-scope and global concurrency limits.  Priority ordering:
    approved_execution > research > status (§7.1).
    """

    async def dispatch(self, work_item: WorkItem, scope_id: str) -> WorkItemResult:
        """Dispatch *work_item* to a pool slot, blocking until a slot is free."""
        ...

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running executor.  Returns ``True`` if cancellation succeeded."""
        ...

    async def shutdown(self) -> None:
        """Wait for all in-flight work items and release resources."""
        ...


@runtime_checkable
class WorktreeManager(Protocol):
    """Manages per-executor git-worktree isolation (§7.4)."""

    async def create(
        self,
        scope_id: str,
        task_id: str,
        attempt: int,
    ) -> str:
        """Create an ephemeral worktree; return its path."""
        ...

    async def merge_back(self, worktree_path: str) -> tuple[bool, str | None]:
        """Three-way-merge changes into the canonical workspace.

        Returns ``(True, None)`` on success, or ``(False, conflict_detail)``
        on merge conflict.
        """
        ...

    async def destroy(self, worktree_path: str) -> None:
        """Remove the worktree and its git metadata."""
        ...


__all__ = [
    "ExecutorPool",
    "PlanParser",
    "VerificationRunner",
    "WorkItemExecutor",
    "WorkItemStore",
    "WorktreeManager",
]
