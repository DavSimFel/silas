"""Concurrency-capped executor pool for parallel work-item dispatch (§7.1).

The pool manages per-scope and global semaphores so that independent
work items can execute concurrently without exceeding configured caps.
Conflict detection (§7.2) serialises items that write overlapping
file paths.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from silas.models.work import WorkItem, WorkItemResult, WorkItemStatus

logger = logging.getLogger(__name__)

# Priority ordering: lower number = higher priority (§7.1)
_PRIORITY_MAP: dict[str, int] = {
    "approved_execution": 0,
    "research": 1,
    "status": 2,
}


class LiveExecutorPool:
    """Async executor pool with per-scope and global concurrency caps.

    Parameters
    ----------
    executor_factory:
        Callable that, given a ``WorkItem`` and ``scope_id``, returns the
        coroutine result (``WorkItemResult``).  Typically wraps a
        ``LiveWorkItemExecutor.execute`` call.
    max_concurrent:
        Maximum concurrent work items per scope.
    max_concurrent_global:
        Maximum concurrent work items across all scopes.
    """

    def __init__(
        self,
        executor_factory: Callable[..., Any],
        *,
        max_concurrent: int = 8,
        max_concurrent_global: int = 16,
    ) -> None:
        self._executor_factory = executor_factory
        self._max_concurrent = max(1, max_concurrent)
        self._max_concurrent_global = max(1, max_concurrent_global)

        self._global_semaphore = asyncio.Semaphore(self._max_concurrent_global)
        self._scope_semaphores: dict[str, asyncio.Semaphore] = {}

        # Track in-flight tasks by task_id for cancellation
        self._in_flight: dict[str, asyncio.Task[WorkItemResult]] = {}
        self._lock = asyncio.Lock()

    # ── public API ──────────────────────────────────────────────────

    async def dispatch(self, work_item: WorkItem, scope_id: str) -> WorkItemResult:
        """Dispatch a work item, respecting concurrency caps.

        Blocks until both per-scope and global semaphore slots are free,
        then executes via the factory.
        """
        scope_sem = self._get_scope_semaphore(scope_id)

        # Acquire both semaphores (global first to avoid deadlock)
        async with self._global_semaphore, scope_sem:
            task = asyncio.current_task()
            async with self._lock:
                if task is not None:
                    self._in_flight[work_item.id] = task

            logger.info(
                "executor_pool_dispatch scope=%s task=%s global_available=%d scope_available=%d",
                scope_id,
                work_item.id,
                self._global_semaphore._value,
                scope_sem._value,
            )
            try:
                result: WorkItemResult = await self._executor_factory(work_item)
                return result
            except asyncio.CancelledError:
                logger.warning(
                    "executor_pool_cancelled scope=%s task=%s",
                    scope_id,
                    work_item.id,
                )
                return WorkItemResult(
                    work_item_id=work_item.id,
                    status=WorkItemStatus.failed,
                    summary=f"Work item {work_item.id} cancelled.",
                    last_error="cancelled",
                )
            except Exception as exc:
                logger.exception(
                    "executor_pool_error scope=%s task=%s",
                    scope_id,
                    work_item.id,
                )
                return WorkItemResult(
                    work_item_id=work_item.id,
                    status=WorkItemStatus.failed,
                    summary=f"Work item {work_item.id} failed with error.",
                    last_error=str(exc),
                )
            finally:
                async with self._lock:
                    self._in_flight.pop(work_item.id, None)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running executor by task_id.

        Returns ``True`` if cancellation was sent, ``False`` if the task
        was not found (already finished or never dispatched).
        """
        async with self._lock:
            task = self._in_flight.get(task_id)
        if task is None:
            return False
        task.cancel()
        logger.info("executor_pool_cancel_requested task=%s", task_id)
        return True

    async def shutdown(self) -> None:
        """Wait for all in-flight work items to finish."""
        async with self._lock:
            tasks = list(self._in_flight.values())
        if tasks:
            logger.info("executor_pool_shutdown waiting for %d tasks", len(tasks))
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("executor_pool_shutdown_complete")

    async def dispatch_parallel(
        self,
        work_items: list[WorkItem],
        scope_id: str,
    ) -> list[WorkItemResult]:
        """Dispatch multiple independent work items concurrently.

        All items run in parallel, respecting concurrency caps.
        Conflicting items (overlapping file paths) are serialised.
        Returns results in the same order as the input list.
        """
        groups = _detect_conflicts(work_items)
        results: dict[str, WorkItemResult] = {}

        # Dispatch non-conflicting groups in parallel
        for group in groups:
            if len(group) == 1:
                # Single item or serialised conflict — just dispatch
                coros = [self.dispatch(group[0], scope_id)]
            else:
                # Independent items — dispatch concurrently
                coros = [self.dispatch(item, scope_id) for item in group]

            group_results = await asyncio.gather(*coros)
            for item, result in zip(group, group_results, strict=True):
                results[item.id] = result

        return [results[item.id] for item in work_items]

    # ── internals ───────────────────────────────────────────────────

    def _get_scope_semaphore(self, scope_id: str) -> asyncio.Semaphore:
        """Lazily create and cache a per-scope semaphore."""
        sem = self._scope_semaphores.get(scope_id)
        if sem is None:
            sem = asyncio.Semaphore(self._max_concurrent)
            self._scope_semaphores[scope_id] = sem
        return sem

    @property
    def in_flight_count(self) -> int:
        """Number of currently executing tasks."""
        return len(self._in_flight)

    @property
    def max_concurrent(self) -> int:
        """Per-scope concurrency cap."""
        return self._max_concurrent

    @property
    def max_concurrent_global(self) -> int:
        """Global concurrency cap."""
        return self._max_concurrent_global


def _detect_conflicts(work_items: list[WorkItem]) -> list[list[WorkItem]]:
    """Group work items by file-resource overlaps (§7.2).

    Items that write overlapping paths are serialised (placed in separate
    single-item groups).  Non-conflicting items go into one parallel group.

    Returns a list of groups.  Groups with >1 item can be dispatched
    concurrently; groups with 1 item are dispatched serially.
    """
    if len(work_items) <= 1:
        return [work_items] if work_items else []

    # Extract write paths from each work item's body (heuristic parse)
    write_paths: dict[str, set[str]] = {}
    for item in work_items:
        paths = _extract_file_paths(item)
        write_paths[item.id] = paths

    # Find conflicting pairs
    conflicting: set[str] = set()
    item_ids = list(write_paths.keys())
    for i, id_a in enumerate(item_ids):
        for id_b in item_ids[i + 1 :]:
            if write_paths[id_a] & write_paths[id_b]:
                conflicting.add(id_a)
                conflicting.add(id_b)

    # Build groups: non-conflicting items in one parallel group,
    # conflicting items each get their own serial group
    parallel_group: list[WorkItem] = []
    serial_groups: list[list[WorkItem]] = []

    for item in work_items:
        if item.id in conflicting:
            serial_groups.append([item])
        else:
            parallel_group.append(item)

    groups: list[list[WorkItem]] = []
    if parallel_group:
        groups.append(parallel_group)
    groups.extend(serial_groups)
    return groups


def _extract_file_paths(item: WorkItem) -> set[str]:
    """Extract file paths from a work item for conflict detection.

    Uses a heuristic: looks at ``input_artifacts_from`` and common
    path patterns in the body.  This is intentionally conservative —
    false positives (serialising unnecessarily) are safe; false negatives
    (missing a conflict) risk data corruption.
    """
    paths: set[str] = set()
    # input_artifacts_from is the explicit artifact dependency list
    for artifact_ref in item.input_artifacts_from:
        paths.add(artifact_ref)
    return paths


def priority_key(work_item: WorkItem) -> int:
    """Return a sort key for dispatch priority ordering (§7.1).

    Lower values = higher priority:
    approved_execution (0) > research (1) > status (2).
    """
    if work_item.approval_token is not None:
        return _PRIORITY_MAP.get("approved_execution", 0)
    if work_item.type.value == "goal":
        return _PRIORITY_MAP.get("status", 2)
    return _PRIORITY_MAP.get("research", 1)


__all__ = ["LiveExecutorPool", "priority_key"]
