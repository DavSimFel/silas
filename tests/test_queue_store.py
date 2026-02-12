"""Tests for the durable queue store, router, and message types.

Covers the full lifecycle, concurrency, crash recovery, and routing
correctness per specs/agent-loop-architecture.md §2.1-2.5.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from silas.queue.router import ROUTE_TABLE, QueueRouter
from silas.queue.store import DurableQueueStore
from silas.queue.types import (
    ExecutionStatus,
    MessageKind,
    QueueMessage,
)


def _make_msg(
    kind: MessageKind = "user_message",
    sender: str = "proxy",
    queue_name: str = "test_queue",
) -> QueueMessage:
    """Helper to create a QueueMessage with sensible defaults."""
    return QueueMessage(
        queue_name=queue_name,
        message_kind=kind,
        sender=sender,
    )


@pytest.fixture
async def store(tmp_path: object) -> DurableQueueStore:
    """Create an initialized DurableQueueStore backed by a temp SQLite DB."""
    # Why str() cast: tmp_path is a pathlib.Path, but store expects str.
    db_path = str(tmp_path / "test_queue.db")  # type: ignore[operator]
    s = DurableQueueStore(db_path)
    await s.initialize()
    return s


@pytest.fixture
async def router(store: DurableQueueStore) -> QueueRouter:
    """Create a QueueRouter wired to the test store."""
    return QueueRouter(store)


class TestFullLifecycle:
    """Enqueue → lease → ack removes the message permanently."""

    async def test_enqueue_lease_ack(self, store: DurableQueueStore) -> None:
        msg = _make_msg()
        await store.enqueue(msg)

        leased = await store.lease("test_queue")
        assert leased is not None
        assert leased.id == msg.id
        assert leased.lease_id is not None

        await store.ack(msg.id)

        # Why lease again: confirms the message is truly gone, not just re-leasable.
        again = await store.lease("test_queue")
        assert again is None

    async def test_lease_empty_queue_returns_none(self, store: DurableQueueStore) -> None:
        result = await store.lease("nonexistent_queue")
        assert result is None


class TestLeaseExpiry:
    """Expired leases allow other consumers to pick up messages."""

    async def test_expired_lease_allows_release(self, store: DurableQueueStore) -> None:
        msg = _make_msg()
        await store.enqueue(msg)

        # Lease with a very short duration.
        leased = await store.lease("test_queue", lease_duration_s=1)
        assert leased is not None

        # Why sleep: we need the lease to actually expire in SQLite's time domain.
        await asyncio.sleep(1.1)

        # Another consumer should be able to lease the same message.
        re_leased = await store.lease("test_queue")
        assert re_leased is not None
        assert re_leased.id == msg.id
        # Why different lease_id: proves it's a new lease, not the old one.
        assert re_leased.lease_id != leased.lease_id


class TestNack:
    """Nack releases the lease and increments attempt_count."""

    async def test_nack_releases_and_increments(self, store: DurableQueueStore) -> None:
        msg = _make_msg()
        await store.enqueue(msg)

        leased = await store.lease("test_queue")
        assert leased is not None
        assert leased.attempt_count == 0

        await store.nack(msg.id)

        # Why lease again: nack should make the message available immediately.
        re_leased = await store.lease("test_queue")
        assert re_leased is not None
        assert re_leased.attempt_count == 1
        assert re_leased.id == msg.id


class TestDeadLetter:
    """Messages moved to dead_letters are removed from the main queue."""

    async def test_dead_letter_removes_from_queue(self, store: DurableQueueStore) -> None:
        msg = _make_msg()
        await store.enqueue(msg)
        await store.lease("test_queue")

        await store.dead_letter(msg.id, reason="max retries exceeded")

        # Why check both: message must be gone from queue AND present in dead_letters.
        leased = await store.lease("test_queue")
        assert leased is None

        # Verify it landed in the dead_letters table.
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM dead_letters WHERE id = ?", (msg.id,))
            row = await cursor.fetchone()
            assert row is not None
            assert row["dead_letter_reason"] == "max retries exceeded"


class TestHeartbeat:
    """Heartbeat extends lease expiry to prevent timeout during long processing."""

    async def test_heartbeat_extends_lease(self, store: DurableQueueStore) -> None:
        msg = _make_msg()
        await store.enqueue(msg)

        # Lease with short duration so it would expire without heartbeat.
        leased = await store.lease("test_queue", lease_duration_s=1)
        assert leased is not None

        # Extend the lease well into the future.
        await store.heartbeat(msg.id, extend_s=300)

        # Why sleep: proves the original 1s lease would have expired,
        # but the heartbeat extended it so no one else can lease it.
        await asyncio.sleep(1.1)

        other = await store.lease("test_queue")
        # Why None: the heartbeat extended the lease, so no message is available.
        assert other is None


class TestIdempotency:
    """has_processed / mark_processed ensure exactly-once side effects."""

    async def test_idempotency_lifecycle(self, store: DurableQueueStore) -> None:
        msg = _make_msg()

        # Why check before mark: this is the exact sequence consumers must follow (§2.2.1).
        assert await store.has_processed("consumer_a", msg.id) is False
        await store.mark_processed("consumer_a", msg.id)
        assert await store.has_processed("consumer_a", msg.id) is True

        # Why different consumer: idempotency is per-consumer, not global.
        assert await store.has_processed("consumer_b", msg.id) is False


class TestStartupRecovery:
    """requeue_expired clears stale leases from a previous crash."""

    async def test_requeue_expired_clears_stale_leases(
        self, store: DurableQueueStore
    ) -> None:
        msg = _make_msg()
        await store.enqueue(msg)

        # Lease with short duration and let it expire.
        await store.lease("test_queue", lease_duration_s=1)
        await asyncio.sleep(1.1)

        count = await store.requeue_expired()
        assert count == 1

        # Why lease again: the requeued message should now be available.
        leased = await store.lease("test_queue")
        assert leased is not None
        assert leased.id == msg.id
        assert leased.lease_id is not None


class TestRouting:
    """All message kinds route to the correct queue per the spec."""

    async def test_all_kinds_route_correctly(
        self, store: DurableQueueStore, router: QueueRouter
    ) -> None:
        for kind, expected_queue in ROUTE_TABLE.items():
            msg = QueueMessage(
                message_kind=kind,  # type: ignore[arg-type]
                sender="runtime",
            )
            await router.route(msg)
            assert msg.queue_name == expected_queue, (
                f"{kind} should route to {expected_queue}, got {msg.queue_name}"
            )

    async def test_route_with_trace_sets_trace_id(
        self, store: DurableQueueStore, router: QueueRouter
    ) -> None:
        msg = _make_msg(kind="plan_request")
        trace = "custom-trace-id-123"
        await router.route_with_trace(msg, trace_id=trace)
        assert msg.trace_id == trace
        assert msg.queue_name == "planner_queue"


class TestConcurrentLease:
    """Two leases on the same queue return different messages."""

    async def test_two_leases_get_different_messages(
        self, store: DurableQueueStore
    ) -> None:
        msg1 = _make_msg()
        msg2 = _make_msg()
        await store.enqueue(msg1)
        await store.enqueue(msg2)

        leased1 = await store.lease("test_queue")
        leased2 = await store.lease("test_queue")

        assert leased1 is not None
        assert leased2 is not None
        # Why set comparison: we don't care about order, just that they're distinct.
        assert {leased1.id, leased2.id} == {msg1.id, msg2.id}


class TestPendingCount:
    """pending_count reflects only unleased messages."""

    async def test_pending_count_accuracy(self, store: DurableQueueStore) -> None:
        assert await store.pending_count("test_queue") == 0

        await store.enqueue(_make_msg())
        await store.enqueue(_make_msg())
        assert await store.pending_count("test_queue") == 2

        # Leasing one should decrease pending count.
        await store.lease("test_queue")
        assert await store.pending_count("test_queue") == 1


class TestMessageTypes:
    """Verify message type construction and enum values."""

    def test_execution_status_values(self) -> None:
        # Why check all values: ensures the enum matches the spec exactly.
        expected = {"running", "done", "failed", "stuck", "blocked", "verification_failed"}
        actual = {s.value for s in ExecutionStatus}
        assert actual == expected

    def test_queue_message_defaults(self) -> None:
        msg = QueueMessage(message_kind="user_message", sender="proxy")
        assert msg.id  # Why: auto-generated UUID should be non-empty.
        assert msg.trace_id
        assert msg.attempt_count == 0
        assert msg.lease_id is None
        assert isinstance(msg.created_at, datetime)
