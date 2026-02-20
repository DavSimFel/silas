"""Tests for WorkItemRunner retry logic and failure escalation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from silas.execution.runner import WorkItemRunner
from silas.models.work import (
    Budget,
    EscalationAction,
    WorkItem,
    WorkItemResult,
    WorkItemStatus,
    WorkItemType,
)


def _make_work_item(
    *,
    on_failure: str = "retry",
    max_attempts: int = 3,
    escalation: dict[str, EscalationAction] | None = None,
) -> WorkItem:
    return WorkItem(
        id="wi-test-001",
        type=WorkItemType.task,
        title="Test work item",
        body="Do the thing",
        budget=Budget(max_attempts=max_attempts),
        on_failure=on_failure,
        needs_approval=False,
        escalation=escalation or {},
        created_at=datetime.now(UTC),
    )


def _success_result(work_item_id: str) -> WorkItemResult:
    return WorkItemResult(
        work_item_id=work_item_id,
        status=WorkItemStatus.done,
        summary="Completed successfully",
    )


def _failure_result(work_item_id: str, error: str = "something broke") -> WorkItemResult:
    return WorkItemResult(
        work_item_id=work_item_id,
        status=WorkItemStatus.failed,
        summary="Failed",
        last_error=error,
    )


class FakeExecutor:
    """Executor that returns configurable results per attempt."""

    def __init__(self, results: list[WorkItemResult]) -> None:
        self._results = list(results)
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    async def execute(self, work_item: WorkItem) -> WorkItemResult:
        idx = min(self._call_count, len(self._results) - 1)
        self._call_count += 1
        return self._results[idx]


class TestRetryOnSuccess:
    @pytest.mark.asyncio
    async def test_succeeds_first_try(self) -> None:
        executor = FakeExecutor([_success_result("wi-test-001")])
        runner = WorkItemRunner(executor, backoff_base_seconds=0.01)
        result = await runner.run(_make_work_item())
        assert result.status == WorkItemStatus.done
        assert executor.call_count == 1

    @pytest.mark.asyncio
    async def test_succeeds_after_retries(self) -> None:
        executor = FakeExecutor(
            [
                _failure_result("wi-test-001"),
                _failure_result("wi-test-001"),
                _success_result("wi-test-001"),
            ]
        )
        runner = WorkItemRunner(executor, backoff_base_seconds=0.01)
        result = await runner.run(_make_work_item(max_attempts=3))
        assert result.status == WorkItemStatus.done
        assert executor.call_count == 3


class TestOnFailureReport:
    @pytest.mark.asyncio
    async def test_no_retry_on_report(self) -> None:
        executor = FakeExecutor([_failure_result("wi-test-001")])
        runner = WorkItemRunner(executor, backoff_base_seconds=0.01)
        result = await runner.run(_make_work_item(on_failure="report", max_attempts=3))
        assert result.status == WorkItemStatus.failed
        assert executor.call_count == 1


class TestOnFailurePause:
    @pytest.mark.asyncio
    async def test_pause_returns_stuck(self) -> None:
        executor = FakeExecutor([_failure_result("wi-test-001")])
        runner = WorkItemRunner(executor, backoff_base_seconds=0.01)
        result = await runner.run(_make_work_item(on_failure="pause"))
        assert result.status == WorkItemStatus.stuck
        assert executor.call_count == 1
        assert "Paused" in result.summary


class TestOnFailureEscalate:
    @pytest.mark.asyncio
    async def test_escalate_retries_once_then_escalates(self) -> None:
        escalation_called: list[str] = []

        async def on_escalate(wi: WorkItem, action: EscalationAction) -> None:
            escalation_called.append(action.action)

        executor = FakeExecutor(
            [
                _failure_result("wi-test-001"),
                _failure_result("wi-test-001"),
            ]
        )
        runner = WorkItemRunner(
            executor,
            backoff_base_seconds=0.01,
            on_escalate=on_escalate,
        )
        result = await runner.run(
            _make_work_item(
                on_failure="escalate",
                max_attempts=5,
                escalation={"default": EscalationAction(action="notify_owner")},
            )
        )
        assert executor.call_count == 2  # one retry, then stop
        assert "Escalated" in result.summary
        assert escalation_called == ["notify_owner"]

    @pytest.mark.asyncio
    async def test_escalate_without_handler(self) -> None:
        executor = FakeExecutor(
            [
                _failure_result("wi-test-001"),
                _failure_result("wi-test-001"),
            ]
        )
        runner = WorkItemRunner(executor, backoff_base_seconds=0.01)
        result = await runner.run(
            _make_work_item(
                on_failure="escalate",
                max_attempts=5,
                escalation={"default": EscalationAction(action="notify_owner")},
            )
        )
        assert executor.call_count == 2
        assert "Escalated" in result.summary


class TestBudgetTracking:
    @pytest.mark.asyncio
    async def test_attempts_tracked(self) -> None:
        executor = FakeExecutor(
            [
                _failure_result("wi-test-001"),
                _success_result("wi-test-001"),
            ]
        )
        runner = WorkItemRunner(executor, backoff_base_seconds=0.01)
        result = await runner.run(_make_work_item(max_attempts=3))
        assert result.budget_used.attempts == 2

    @pytest.mark.asyncio
    async def test_max_attempts_exhausted(self) -> None:
        executor = FakeExecutor([_failure_result("wi-test-001")] * 5)
        runner = WorkItemRunner(executor, backoff_base_seconds=0.01)
        result = await runner.run(_make_work_item(on_failure="retry", max_attempts=3))
        assert result.status == WorkItemStatus.failed
        assert executor.call_count == 3


class TestBackoff:
    def test_backoff_capped(self) -> None:
        runner = WorkItemRunner(
            FakeExecutor([]),
            backoff_base_seconds=1.0,
            backoff_max_seconds=10.0,
        )
        assert runner._backoff_delay(1) == 1.0
        assert runner._backoff_delay(2) == 2.0
        assert runner._backoff_delay(3) == 4.0
        assert runner._backoff_delay(4) == 8.0
        assert runner._backoff_delay(5) == 10.0  # capped
