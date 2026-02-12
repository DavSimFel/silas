"""WorkItemRunner — retry loop and failure escalation for work items.

Wraps an executor to provide:
- Configurable retry with exponential backoff
- Budget tracking (attempt counting)
- on_failure escalation (report, retry, escalate, pause)
- Failure context propagation for diagnostics
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from silas.models.work import (
    BudgetUsed,
    EscalationAction,
    WorkItem,
    WorkItemResult,
    WorkItemStatus,
)

logger = logging.getLogger(__name__)


class WorkExecutor(Protocol):
    """Protocol for the underlying executor that runs a single work item attempt."""

    async def execute(self, work_item: WorkItem) -> WorkItemResult: ...


class WorkItemRunner:
    """Runs work items with retry, budget tracking, and failure escalation.

    Sits between the Stream's execution loop and the raw WorkExecutor.
    Handles the retry/escalation logic that the spec defines but wasn't wired up.
    """

    def __init__(
        self,
        executor: WorkExecutor,
        *,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 30.0,
        on_escalate: Callable[[WorkItem, EscalationAction], Awaitable[None]] | None = None,
    ) -> None:
        self._executor = executor
        self._backoff_base = backoff_base_seconds
        self._backoff_max = backoff_max_seconds
        self._on_escalate = on_escalate

    async def run(self, work_item: WorkItem) -> WorkItemResult:
        """Execute a work item with retry logic based on its budget and on_failure policy."""
        max_attempts = work_item.budget.max_attempts
        last_result: WorkItemResult | None = None

        for attempt in range(1, max_attempts + 1):
            # Update attempt count on the work item
            work_item = work_item.model_copy(update={"attempts": attempt})

            result = await self._executor.execute(work_item)

            # Track budget usage
            result = result.model_copy(
                update={
                    "budget_used": BudgetUsed(
                        attempts=attempt,
                        tokens=result.budget_used.tokens,
                        cost_usd=result.budget_used.cost_usd,
                        wall_time_seconds=result.budget_used.wall_time_seconds,
                        planner_calls=result.budget_used.planner_calls,
                        executor_runs=result.budget_used.executor_runs,
                    ),
                },
            )

            if result.status == WorkItemStatus.done:
                logger.info(
                    "Work item %s completed on attempt %d/%d",
                    work_item.id, attempt, max_attempts,
                )
                return result

            last_result = result

            # Check if we should retry based on on_failure policy
            should_retry = self._should_retry(work_item, attempt, max_attempts)
            if not should_retry:
                break

            # Backoff before next attempt
            delay = self._backoff_delay(attempt)
            logger.info(
                "Work item %s failed on attempt %d/%d, retrying in %.1fs: %s",
                work_item.id, attempt, max_attempts, delay,
                result.last_error or result.summary,
            )
            await asyncio.sleep(delay)

        # All attempts exhausted or policy says stop
        assert last_result is not None
        return await self._handle_failure(work_item, last_result)

    def _should_retry(self, work_item: WorkItem, attempt: int, max_attempts: int) -> bool:
        """Determine if the work item should be retried based on policy and budget."""
        if attempt >= max_attempts:
            return False

        on_failure = work_item.on_failure
        # "report" = no retry, just report the failure
        # "retry" = retry up to max_attempts
        # "escalate" = retry once then escalate
        # "pause" = no retry, pause for human intervention
        if on_failure == "report":
            return False
        if on_failure == "pause":
            return False
        if on_failure == "escalate":
            # Escalate after first failure — allow one retry before escalating
            return attempt < 2
        # Default: "retry" or unknown policies get retry behavior
        return True

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with cap."""
        delay = self._backoff_base * (2 ** (attempt - 1))
        return min(delay, self._backoff_max)

    async def _handle_failure(
        self, work_item: WorkItem, result: WorkItemResult,
    ) -> WorkItemResult:
        """Handle final failure based on on_failure policy."""
        on_failure = work_item.on_failure
        failure_context = (
            f"Work item '{work_item.title}' failed after {result.budget_used.attempts} attempts. "
            f"Last error: {result.last_error or result.summary}"
        )

        if on_failure == "pause":
            logger.warning("Work item %s paused for human intervention", work_item.id)
            return result.model_copy(
                update={
                    "status": WorkItemStatus.stuck,
                    "summary": f"Paused: {failure_context}",
                },
            )

        if on_failure == "escalate":
            escalation = work_item.escalation.get("default")
            if escalation is not None and self._on_escalate is not None:
                logger.warning(
                    "Escalating work item %s: %s", work_item.id, escalation.action,
                )
                try:
                    await self._on_escalate(work_item, escalation)
                except (ValueError, RuntimeError, OSError) as exc:
                    logger.warning(
                        "Escalation failed for work item %s: %s", work_item.id, exc,
                    )
            return result.model_copy(
                update={"summary": f"Escalated: {failure_context}"},
            )

        # Default "report" — just return the failure as-is
        return result.model_copy(
            update={
                "summary": failure_context,
                "status": WorkItemStatus.failed,
            },
        )


__all__ = ["WorkExecutor", "WorkItemRunner"]
