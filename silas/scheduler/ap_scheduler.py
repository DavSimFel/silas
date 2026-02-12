"""SilasScheduler — thin wrapper around APScheduler's AsyncIOScheduler.

Provides a clean interface for:
- Goal cron schedules (e.g. "*/30 * * * *" for periodic verification)
- Heartbeat intervals (e.g. suggestion engine every 60s)
- Lifecycle management (start/stop)

All callbacks are async-compatible. The scheduler runs in the existing
asyncio event loop — no extra threads for job execution.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Callback type: async function with no args, returning anything
AsyncCallback = Callable[[], Coroutine[Any, Any, Any]]


class SilasScheduler:
    """Manages cron and interval schedules for the Silas runtime.

    Wraps APScheduler v3's AsyncIOScheduler so the rest of the codebase
    doesn't depend on APScheduler internals directly.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        # Track our job IDs so we can list/remove them
        self._jobs: dict[str, str] = {}  # our_id → apscheduler_job_id
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def job_ids(self) -> list[str]:
        """Return all registered schedule IDs."""
        return list(self._jobs.keys())

    def add_goal_schedule(
        self,
        goal_id: str,
        cron_expr: str,
        callback: AsyncCallback,
    ) -> str:
        """Register a goal's cron schedule.

        Args:
            goal_id: Unique identifier for the goal (used as schedule ID).
            cron_expr: Standard cron expression (5-field: min hour dom month dow).
            callback: Async function to invoke on each trigger.

        Returns:
            The schedule ID (same as goal_id).

        Raises:
            ValueError: If goal_id is already registered or cron_expr is invalid.
        """
        schedule_id = f"goal:{goal_id}"

        if schedule_id in self._jobs:
            raise ValueError(f"schedule '{schedule_id}' already registered")

        # Parse cron expression into APScheduler trigger
        trigger = CronTrigger.from_crontab(cron_expr)

        job = self._scheduler.add_job(
            self._safe_invoke(callback, schedule_id),
            trigger=trigger,
            id=schedule_id,
            name=f"goal-{goal_id}",
            replace_existing=False,
        )
        self._jobs[schedule_id] = job.id
        logger.info("Registered goal schedule: %s (%s)", schedule_id, cron_expr)
        return schedule_id

    def add_heartbeat(
        self,
        name: str,
        interval_seconds: int,
        callback: AsyncCallback,
    ) -> str:
        """Register a periodic heartbeat.

        Args:
            name: Human-readable name (e.g. "suggestion_engine").
            interval_seconds: Seconds between invocations. Must be > 0.
            callback: Async function to invoke on each tick.

        Returns:
            The schedule ID.

        Raises:
            ValueError: If name is already registered or interval is invalid.
        """
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")

        schedule_id = f"heartbeat:{name}"

        if schedule_id in self._jobs:
            raise ValueError(f"schedule '{schedule_id}' already registered")

        trigger = IntervalTrigger(seconds=interval_seconds)

        job = self._scheduler.add_job(
            self._safe_invoke(callback, schedule_id),
            trigger=trigger,
            id=schedule_id,
            name=f"heartbeat-{name}",
            replace_existing=False,
        )
        self._jobs[schedule_id] = job.id
        logger.info("Registered heartbeat: %s (every %ds)", schedule_id, interval_seconds)
        return schedule_id

    def remove_schedule(self, schedule_id: str) -> None:
        """Remove a registered schedule.

        Args:
            schedule_id: The ID returned by add_goal_schedule or add_heartbeat.

        Raises:
            KeyError: If the schedule ID is not registered.
        """
        if schedule_id not in self._jobs:
            raise KeyError(f"unknown schedule: {schedule_id}")

        try:
            self._scheduler.remove_job(schedule_id)
        except Exception:  # noqa: BLE001
            # Job might already have been removed by APScheduler (one-shot, etc.)
            logger.debug("Job %s already removed from APScheduler", schedule_id)

        del self._jobs[schedule_id]
        logger.info("Removed schedule: %s", schedule_id)

    def start(self) -> None:
        """Start the scheduler. Idempotent — safe to call if already running."""
        if self._running:
            return
        self._scheduler.start()
        self._running = True
        logger.info("Scheduler started with %d jobs", len(self._jobs))

    def stop(self) -> None:
        """Stop the scheduler and clear all jobs. Idempotent."""
        if not self._running:
            return
        self._scheduler.shutdown(wait=False)
        self._jobs.clear()
        self._running = False
        logger.info("Scheduler stopped")

    def _safe_invoke(
        self,
        callback: AsyncCallback,
        schedule_id: str,
    ) -> AsyncCallback:
        """Wrap a callback so exceptions don't crash the scheduler.

        APScheduler catches exceptions in jobs, but we want structured logging
        so failures are visible in the audit trail.
        """

        async def _wrapper() -> None:
            try:
                await callback()
            except Exception:
                logger.exception("Schedule %s callback failed", schedule_id)

        return _wrapper


__all__ = ["SilasScheduler"]
