"""Queue store benchmarks — enqueue/lease/ack throughput.

Why benchmark the queue: it's the central nervous system of the agent loop.
Degraded queue performance directly impacts turn latency. These benchmarks
establish a baseline so regressions are caught before they hit production.
"""

from __future__ import annotations

import tempfile

from silas.benchmarks.runner import benchmark
from silas.queue.store import DurableQueueStore
from silas.queue.types import QueueMessage


def _make_msg(queue_name: str = "bench_queue") -> QueueMessage:
    return QueueMessage(
        queue_name=queue_name,
        message_kind="user_message",
        sender="bench",
        payload={"text": "benchmark payload"},
    )


async def _fresh_store() -> DurableQueueStore:
    """Create an ephemeral SQLite queue store for one benchmark iteration."""
    # Why TemporaryDirectory: avoids SIM115 and gives us a clean path for SQLite.
    tmpdir = tempfile.mkdtemp()
    store = DurableQueueStore(f"{tmpdir}/bench.db")
    await store.initialize()
    return store


@benchmark(name="queue.enqueue", tags=["queue", "write"], iterations=50)
async def bench_enqueue() -> None:
    """Measure raw enqueue throughput — 100 messages per iteration."""
    store = await _fresh_store()
    for _ in range(100):
        await store.enqueue(_make_msg())


@benchmark(name="queue.lease", tags=["queue", "read"], iterations=50)
async def bench_lease() -> None:
    """Measure lease acquisition after pre-filling the queue."""
    store = await _fresh_store()
    for _ in range(100):
        await store.enqueue(_make_msg())
    for _ in range(100):
        await store.lease("bench_queue", "bench_consumer")


@benchmark(name="queue.enqueue_lease_ack", tags=["queue", "lifecycle"], iterations=50)
async def bench_full_lifecycle() -> None:
    """Full message lifecycle: enqueue → lease → ack."""
    store = await _fresh_store()
    for _ in range(50):
        msg = _make_msg()
        await store.enqueue(msg)
        leased = await store.lease("bench_queue", "bench_consumer")
        if leased:
            await store.ack(leased.id)
