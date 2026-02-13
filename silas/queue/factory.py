"""Factory function to wire the complete queue system.

Creates and connects all queue components (store, router, consumers,
orchestrator, bridge) in the correct dependency order. This is the
single entry point for runtime startup to initialize the queue subsystem.

Why a factory function instead of a class: there's no state to manage
beyond construction. A function is simpler and makes the dependency
graph explicit in the parameter list.
"""

from __future__ import annotations

from silas.queue.bridge import QueueBridge
from silas.queue.consumers import (
    ExecutorAgentProtocol,
    ExecutorConsumer,
    PlannerAgentProtocol,
    PlannerConsumer,
    ProxyAgentProtocol,
    ProxyConsumer,
)
from silas.queue.orchestrator import QueueOrchestrator
from silas.queue.router import QueueRouter
from silas.queue.store import DurableQueueStore


async def create_queue_system(
    db_path: str,
    proxy_agent: ProxyAgentProtocol,
    planner_agent: PlannerAgentProtocol,
    executor_agent: ExecutorAgentProtocol,
) -> tuple[QueueOrchestrator, QueueBridge]:
    """Create store, router, consumers, orchestrator, and bridge.

    Initializes the SQLite-backed queue store (creating tables if needed),
    wires up all three agent consumers, and returns the orchestrator
    (for lifecycle management) and bridge (for Stream integration).

    Args:
        db_path: Path to the SQLite database file for queue persistence.
        proxy_agent: Agent that handles user messages and routing decisions.
        planner_agent: Agent that creates execution plans from requests.
        executor_agent: Agent that executes tasks and research queries.

    Returns:
        Tuple of (orchestrator, bridge). Caller must call orchestrator.start()
        to begin consuming messages.
    """
    store = DurableQueueStore(db_path)
    await store.initialize()

    # Why requeue on startup: if a previous process crashed mid-lease,
    # those messages would be stuck until lease expiry. Requeuing them
    # immediately ensures no messages are lost on restart.
    await store.requeue_expired()

    router = QueueRouter(store)

    proxy_consumer = ProxyConsumer(store, router, proxy_agent)
    planner_consumer = PlannerConsumer(store, router, planner_agent)
    executor_consumer = ExecutorConsumer(store, router, executor_agent)

    orchestrator = QueueOrchestrator(
        store=store,
        router=router,
        consumers=[proxy_consumer, planner_consumer, executor_consumer],
    )

    bridge = QueueBridge(
        orchestrator=orchestrator,
        router=router,
        store=store,
    )

    return orchestrator, bridge


__all__ = ["create_queue_system"]
