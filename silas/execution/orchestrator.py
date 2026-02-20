"""Queue orchestrator — runs all consumers as concurrent async tasks.

Manages coordinated startup/shutdown and shared backoff logic for all
queue consumers. The orchestrator is the single entry point for the
queue-based execution path.

Why an orchestrator: consumers need coordinated lifecycle management,
exponential backoff when queues are empty, and a single place to wire
all dependencies (agents, stores, router).
"""

from __future__ import annotations

import asyncio
import logging

from silas.execution.consumers import BaseConsumer
from silas.execution.queue_store import DurableQueueStore
from silas.execution.router import QueueRouter

logger = logging.getLogger(__name__)

# Why 5s max backoff: aggressive enough to be responsive when messages
# arrive, gentle enough to avoid hammering SQLite when idle. The reset
# on message-found ensures we snap back to low latency immediately.
_MAX_BACKOFF_S = 5.0
_BACKOFF_MULTIPLIER = 2.0


class QueueOrchestrator:
    """Manages all queue consumers as concurrent async tasks.

    Consumers poll their queues in a loop with exponential backoff.
    The orchestrator starts them as background tasks and provides
    graceful shutdown that waits for in-flight messages to finish.
    """

    def __init__(
        self,
        store: DurableQueueStore,
        router: QueueRouter,
        consumers: list[BaseConsumer],
        *,
        poll_interval_s: float = 0.1,
    ) -> None:
        self._store = store
        self._router = router
        self._consumers = consumers
        self._poll_interval_s = poll_interval_s
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

    @property
    def running(self) -> bool:
        """Whether the orchestrator is currently running."""
        return self._running

    async def start(self) -> None:
        """Start all consumers as background asyncio tasks.

        Called from Stream startup when queue_execution is enabled.
        Idempotent — calling start() twice is safe (second call is a no-op).
        """
        if self._running:
            return

        self._running = True
        for consumer in self._consumers:
            task = asyncio.create_task(
                self._run_consumer(consumer),
                name=f"consumer:{consumer.queue_name}",
            )
            self._tasks.append(task)

        logger.info("QueueOrchestrator started %d consumers", len(self._consumers))

    async def stop(self) -> None:
        """Graceful shutdown: stop polling and wait for in-flight messages.

        Sets the running flag to False so poll loops exit after their current
        iteration, then awaits all tasks to ensure clean shutdown.
        """
        if not self._running:
            return

        self._running = False

        # Why gather with return_exceptions: we want all tasks to finish
        # even if some raise. Exceptions are logged, not propagated.
        if self._tasks:
            results = await asyncio.gather(*self._tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    logger.error("Consumer task failed during shutdown: %s", result)

        self._tasks.clear()
        logger.info("QueueOrchestrator stopped")

    async def _run_consumer(
        self,
        consumer: BaseConsumer,
    ) -> None:
        """Poll loop with exponential backoff when queue is empty.

        Starts at poll_interval_s, backs off by 2x to max 5s when no
        messages are found. Resets to base interval on message found.

        Why backoff: when nothing is happening, we don't want to hammer
        SQLite with rapid polls. The backoff is gentle enough that latency
        stays under 5s even in the worst case.
        """
        current_interval = self._poll_interval_s

        while self._running:
            try:
                found = await consumer.poll_once()
            except Exception:
                logger.exception(
                    "Consumer %s poll_once raised unexpectedly",
                    consumer.queue_name,
                )
                found = False

            if found:
                # Reset backoff on successful message processing.
                current_interval = self._poll_interval_s
            else:
                # Exponential backoff when queue is empty.
                current_interval = min(
                    current_interval * _BACKOFF_MULTIPLIER,
                    _MAX_BACKOFF_S,
                )

            await asyncio.sleep(current_interval)


__all__ = ["QueueOrchestrator"]
