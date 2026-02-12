"""SQLite-backed durable queue with lease semantics for crash recovery.

Implements the DurableQueueStore contract from specs/agent-loop-architecture.md
§2.2. Messages are persisted to SQLite so they survive process restarts.
Lease-based consumption ensures at-least-once delivery: if a consumer crashes
mid-processing, the lease expires and another consumer can pick up the message.

Why SQLite: Silas is a single-process runtime. SQLite gives us ACID durability
without an external broker dependency. The aiosqlite wrapper provides async
compatibility with the rest of the codebase.

Why a single connection per operation (context manager pattern): matches the
existing persistence stores (work_item_store, chronicle_store). For the
expected throughput (<100 msgs/sec), connection-per-op is fine and avoids
connection lifecycle complexity.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import aiosqlite

from silas.queue.types import QueueMessage

# Why ISO format with 'T' separator: SQLite stores datetimes as text,
# and ISO 8601 sorts lexicographically which matters for ORDER BY created_at.
_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


def _now_utc() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(UTC)


def _dt_to_str(dt: datetime) -> str:
    """Serialize datetime to ISO 8601 string for SQLite storage."""
    return dt.strftime(_DATETIME_FORMAT)


def _str_to_dt(s: str) -> datetime:
    """Deserialize ISO 8601 string from SQLite back to datetime."""
    return datetime.fromisoformat(s)


def _row_to_message(row: aiosqlite.Row) -> QueueMessage:
    """Reconstruct a QueueMessage from a SQLite row.

    Why manual reconstruction instead of model_validate: we need to handle
    the JSON-encoded payload and datetime string conversions explicitly.
    """
    return QueueMessage(
        id=row["id"],
        queue_name=row["queue_name"],
        message_kind=row["message_kind"],
        sender=row["sender"],
        trace_id=row["trace_id"],
        payload=json.loads(row["payload"]),
        created_at=_str_to_dt(row["created_at"]),
        lease_id=row["lease_id"],
        lease_expires_at=_str_to_dt(row["lease_expires_at"]) if row["lease_expires_at"] else None,
        attempt_count=row["attempt_count"],
    )


class DurableQueueStore:
    """Persistent message queue backed by SQLite with lease-based consumption.

    Lifecycle: enqueue → lease → (ack | nack) → (dead_letter if max attempts).
    On startup, call initialize() to create tables and requeue_expired() to
    recover from any previous crash.

    Each queue is identified by name (e.g., 'proxy_queue', 'planner_queue').
    Messages are ordered FIFO by created_at within each queue.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def initialize(self) -> None:
        """Create queue tables if they don't exist.

        Called once on runtime startup. Idempotent via IF NOT EXISTS.
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Why separate tables for queue_messages vs dead_letters: dead
            # letters are kept indefinitely for debugging, while queue_messages
            # are deleted on ack. Mixing them would complicate lease queries.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS queue_messages (
                    id TEXT PRIMARY KEY,
                    queue_name TEXT NOT NULL,
                    message_kind TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    lease_id TEXT,
                    lease_expires_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5
                )
            """)
            # Why index on queue_name + lease: the lease query filters by
            # queue_name and lease state, so this index makes it efficient.
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_queue_messages_lease
                ON queue_messages (queue_name, lease_id, lease_expires_at, created_at)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS dead_letters (
                    id TEXT PRIMARY KEY,
                    queue_name TEXT NOT NULL,
                    message_kind TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    lease_id TEXT,
                    lease_expires_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    dead_letter_reason TEXT NOT NULL,
                    dead_lettered_at TEXT NOT NULL
                )
            """)
            # Why unique constraint on (consumer, message_id): the idempotency
            # contract (§2.2.1) requires exactly-once processing per consumer.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS processed_messages (
                    consumer TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    PRIMARY KEY (consumer, message_id)
                )
            """)
            await db.commit()

    async def enqueue(self, msg: QueueMessage) -> None:
        """Insert a message into the queue.

        The message's queue_name must already be set (by the router or caller).
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO queue_messages
                   (id, queue_name, message_kind, sender, trace_id, payload,
                    created_at, lease_id, lease_expires_at, attempt_count, max_attempts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg.id,
                    msg.queue_name,
                    msg.message_kind,
                    msg.sender,
                    msg.trace_id,
                    json.dumps(msg.payload),
                    _dt_to_str(msg.created_at),
                    None,
                    None,
                    msg.attempt_count,
                    5,
                ),
            )
            await db.commit()

    async def lease(
        self, queue_name: str, lease_duration_s: int = 60
    ) -> QueueMessage | None:
        """Atomically lease the oldest available message from a queue.

        A message is available if it has no lease or its lease has expired.
        Returns None if no messages are available.

        Why UPDATE+RETURNING instead of SELECT+UPDATE: atomic lease prevents
        two consumers from grabbing the same message in concurrent scenarios.
        SQLite serializes writes, so this is safe even with multiple coroutines.
        """
        now = _now_utc()
        now_str = _dt_to_str(now)
        lease_id = str(uuid.uuid4())
        expires_str = _dt_to_str(
            now + __import__("datetime").timedelta(seconds=lease_duration_s)
        )

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Why subquery with MIN(rowid): SQLite doesn't support
            # UPDATE ... ORDER BY ... LIMIT 1 RETURNING in all versions.
            # Using a subquery to find the target row is universally supported.
            cursor = await db.execute(
                """UPDATE queue_messages
                   SET lease_id = ?, lease_expires_at = ?
                   WHERE id = (
                       SELECT id FROM queue_messages
                       WHERE queue_name = ?
                         AND (lease_id IS NULL OR lease_expires_at < ?)
                       ORDER BY created_at
                       LIMIT 1
                   )
                   RETURNING *""",
                (lease_id, expires_str, queue_name, now_str),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await db.commit()
            return _row_to_message(row)

    async def ack(self, message_id: str) -> None:
        """Remove a successfully processed message from the queue.

        Per §2.2: ack means the consumer has finished all side effects
        and called mark_processed. The message is permanently deleted.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM queue_messages WHERE id = ?", (message_id,))
            await db.commit()

    async def nack(self, message_id: str) -> None:
        """Release a message's lease and increment its attempt count.

        The message returns to the queue for another consumer to pick up.
        If the consumer failed to process it, nack allows retry.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE queue_messages
                   SET lease_id = NULL, lease_expires_at = NULL,
                       attempt_count = attempt_count + 1
                   WHERE id = ?""",
                (message_id,),
            )
            await db.commit()

    async def dead_letter(self, message_id: str, reason: str) -> None:
        """Move a message to the dead letter table.

        Called when a message has exceeded max_attempts or is otherwise
        unprocessable. Preserves the full message for debugging.
        """
        now_str = _dt_to_str(_now_utc())
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM queue_messages WHERE id = ?", (message_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return
            await db.execute(
                """INSERT INTO dead_letters
                   (id, queue_name, message_kind, sender, trace_id, payload,
                    created_at, lease_id, lease_expires_at, attempt_count,
                    max_attempts, dead_letter_reason, dead_lettered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["id"],
                    row["queue_name"],
                    row["message_kind"],
                    row["sender"],
                    row["trace_id"],
                    row["payload"],
                    row["created_at"],
                    row["lease_id"],
                    row["lease_expires_at"],
                    row["attempt_count"],
                    row["max_attempts"],
                    reason,
                    now_str,
                ),
            )
            await db.execute("DELETE FROM queue_messages WHERE id = ?", (message_id,))
            await db.commit()

    async def heartbeat(self, message_id: str, extend_s: int = 60) -> None:
        """Extend a message's lease to prevent expiry during long processing.

        Per §2.2.2: consumers with runs longer than lease_duration/3 must
        send periodic heartbeats to signal they're still alive.
        """
        new_expires = _dt_to_str(
            _now_utc() + __import__("datetime").timedelta(seconds=extend_s)
        )
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE queue_messages SET lease_expires_at = ? WHERE id = ?",
                (new_expires, message_id),
            )
            await db.commit()

    async def has_processed(self, consumer: str, message_id: str) -> bool:
        """Check if a consumer has already processed a message.

        Per §2.2.1: every consumer must check this before executing side
        effects to ensure exactly-once processing semantics.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM processed_messages WHERE consumer = ? AND message_id = ?",
                (consumer, message_id),
            )
            return await cursor.fetchone() is not None

    async def mark_processed(self, consumer: str, message_id: str) -> None:
        """Record that a consumer has successfully processed a message.

        Must be called after side effects succeed but before ack, per §2.2.1.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO processed_messages (consumer, message_id, processed_at)
                   VALUES (?, ?, ?)""",
                (consumer, message_id, _dt_to_str(_now_utc())),
            )
            await db.commit()

    async def pending_count(self, queue_name: str) -> int:
        """Count unleased messages in a queue. Used for telemetry/monitoring."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """SELECT COUNT(*) FROM queue_messages
                   WHERE queue_name = ? AND lease_id IS NULL""",
                (queue_name,),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def requeue_expired(self) -> int:
        """Release all expired leases. Called on startup for crash recovery.

        Any message whose lease has expired is assumed to be from a crashed
        consumer. We clear the lease so it can be picked up again.
        Returns the number of messages requeued.
        """
        now_str = _dt_to_str(_now_utc())
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """UPDATE queue_messages
                   SET lease_id = NULL, lease_expires_at = NULL
                   WHERE lease_id IS NOT NULL AND lease_expires_at < ?""",
                (now_str,),
            )
            count = cursor.rowcount
            await db.commit()
            return count


__all__ = ["DurableQueueStore"]
