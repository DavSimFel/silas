"""Tests for §2.1 QueueMessage schema alignment.

Validates typed fields, typed payloads, backward compatibility with old
messages, and SQLite schema migration for the new first-class fields.
"""

from __future__ import annotations

import json
import tempfile

import aiosqlite
import pytest
from silas.queue.store import DurableQueueStore
from silas.queue.types import (
    AgentResponsePayload,
    ExecutionRequestPayload,
    PlanRequestPayload,
    QueueMessage,
    StatusPayload,
    TaintLevel,
    UserMessagePayload,
    parse_payload,
)

# ── QueueMessage typed fields ───────────────────────────────────────


class TestQueueMessageTypedFields:
    """§2.1 first-class fields are properly typed and default to None."""

    def test_defaults(self) -> None:
        msg = QueueMessage(message_kind="user_message", sender="user")
        assert msg.scope_id is None
        assert msg.taint is None
        assert msg.task_id is None
        assert msg.parent_task_id is None
        assert msg.work_item_id is None
        assert msg.approval_token is None
        assert msg.urgency == "informational"

    def test_all_fields_set(self) -> None:
        msg = QueueMessage(
            message_kind="execution_request",
            sender="runtime",
            scope_id="scope-abc",
            taint=TaintLevel.untrusted,
            task_id="task-1",
            parent_task_id="task-0",
            work_item_id="wi-42",
            approval_token="tok-xyz",
            urgency="needs_attention",
        )
        assert msg.scope_id == "scope-abc"
        assert msg.taint == TaintLevel.untrusted
        assert msg.task_id == "task-1"
        assert msg.parent_task_id == "task-0"
        assert msg.work_item_id == "wi-42"
        assert msg.approval_token == "tok-xyz"
        assert msg.urgency == "needs_attention"

    def test_taint_level_values(self) -> None:
        """TaintLevel enum matches the spec's three levels."""
        assert set(TaintLevel) == {
            TaintLevel.owner,
            TaintLevel.trusted,
            TaintLevel.untrusted,
        }

    def test_json_roundtrip_preserves_typed_fields(self) -> None:
        """Serialize to JSON and back — typed fields must survive."""
        msg = QueueMessage(
            message_kind="user_message",
            sender="user",
            scope_id="s1",
            taint=TaintLevel.trusted,
            task_id="t1",
            urgency="background",
        )
        data = msg.model_dump()
        restored = QueueMessage.model_validate(data)
        assert restored.scope_id == "s1"
        assert restored.taint == TaintLevel.trusted
        assert restored.task_id == "t1"
        assert restored.urgency == "background"


# ── Typed payload parsing ───────────────────────────────────────────


class TestTypedPayloads:
    """parse_payload and typed_payload() return correct models."""

    def test_user_message_payload(self) -> None:
        result = parse_payload("user_message", {"text": "hello", "metadata": {"k": "v"}})
        assert isinstance(result, UserMessagePayload)
        assert result.text == "hello"
        assert result.metadata == {"k": "v"}

    def test_plan_request_payload(self) -> None:
        result = parse_payload("plan_request", {"user_request": "do X", "reason": "because"})
        assert isinstance(result, PlanRequestPayload)
        assert result.user_request == "do X"

    def test_execution_request_payload(self) -> None:
        result = parse_payload(
            "execution_request",
            {"work_item_id": "wi-1", "task_description": "build it"},
        )
        assert isinstance(result, ExecutionRequestPayload)
        assert result.work_item_id == "wi-1"

    def test_agent_response_payload(self) -> None:
        result = parse_payload("agent_response", {"text": "done"})
        assert isinstance(result, AgentResponsePayload)
        assert result.text == "done"

    def test_execution_status_payload(self) -> None:
        result = parse_payload(
            "execution_status",
            {"status": "done", "work_item_id": "wi-1", "attempt": 1},
        )
        assert isinstance(result, StatusPayload)
        assert result.status == "done"

    def test_unknown_kind_returns_none(self) -> None:
        assert parse_payload("system_event", {"foo": "bar"}) is None

    def test_invalid_payload_returns_none(self) -> None:
        """Backward compat: malformed payloads don't crash, just return None."""
        assert parse_payload("user_message", {"wrong_key": 1}) is None

    def test_typed_payload_method(self) -> None:
        msg = QueueMessage(
            message_kind="user_message",
            sender="user",
            payload={"text": "hi"},
        )
        tp = msg.typed_payload()
        assert isinstance(tp, UserMessagePayload)
        assert tp.text == "hi"


# ── Backward compatibility ──────────────────────────────────────────


class TestBackwardCompat:
    """Old messages without new fields must still deserialize."""

    def test_old_style_message_still_works(self) -> None:
        """Simulate an old message dict with only the original 10 fields."""
        old_data = {
            "id": "old-id",
            "queue_name": "proxy_queue",
            "message_kind": "user_message",
            "sender": "user",
            "trace_id": "tr-old",
            "payload": {"text": "legacy"},
            "created_at": "2025-01-01T00:00:00+00:00",
            "lease_id": None,
            "lease_expires_at": None,
            "attempt_count": 0,
        }
        msg = QueueMessage.model_validate(old_data)
        assert msg.scope_id is None
        assert msg.taint is None
        assert msg.urgency == "informational"
        assert msg.payload["text"] == "legacy"


# ── SQLite migration ────────────────────────────────────────────────


@pytest.fixture
async def store() -> DurableQueueStore:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = DurableQueueStore(db_path)
    await s.initialize()
    return s


class TestStoreMigration:
    """New columns are created on initialize and data round-trips."""

    async def test_new_columns_exist(self, store: DurableQueueStore) -> None:
        """After initialize, queue_messages has scope_id and taint columns."""
        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute("PRAGMA table_info(queue_messages)")
            cols = {row[1] for row in await cursor.fetchall()}
        for col in ("scope_id", "taint", "task_id", "parent_task_id",
                     "work_item_id", "approval_token", "urgency"):
            assert col in cols, f"Missing column: {col}"

    async def test_enqueue_lease_roundtrip_with_new_fields(
        self, store: DurableQueueStore
    ) -> None:
        """Typed fields survive enqueue → lease cycle through SQLite."""
        msg = QueueMessage(
            message_kind="execution_request",
            sender="runtime",
            queue_name="executor_queue",
            scope_id="scope-test",
            taint=TaintLevel.untrusted,
            task_id="task-99",
            parent_task_id="task-0",
            work_item_id="wi-7",
            approval_token="tok-abc",
            urgency="needs_attention",
            payload={"task_description": "do stuff"},
        )
        await store.enqueue(msg)
        leased = await store.lease("executor_queue")
        assert leased is not None
        assert leased.scope_id == "scope-test"
        assert leased.taint == TaintLevel.untrusted
        assert leased.task_id == "task-99"
        assert leased.parent_task_id == "task-0"
        assert leased.work_item_id == "wi-7"
        assert leased.approval_token == "tok-abc"
        assert leased.urgency == "needs_attention"

    async def test_migrate_existing_db_without_new_columns(self) -> None:
        """Simulate a pre-migration DB and verify migration adds columns."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        # Create old-style schema without new columns
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE queue_messages (
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
            await db.execute("""
                CREATE TABLE dead_letters (
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
            await db.execute("""
                CREATE TABLE processed_messages (
                    consumer TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    PRIMARY KEY (consumer, message_id)
                )
            """)
            # Insert an old-style message
            await db.execute(
                """INSERT INTO queue_messages
                   (id, queue_name, message_kind, sender, trace_id, payload,
                    created_at, attempt_count, max_attempts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("old-msg", "proxy_queue", "user_message", "user", "tr-1",
                 json.dumps({"text": "legacy"}),
                 "2025-01-01T00:00:00.000000+00:00", 0, 5),
            )
            await db.commit()

        # Now initialize with new code — should migrate
        s = DurableQueueStore(db_path)
        await s.initialize()

        # Verify columns were added
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info(queue_messages)")
            cols = {row[1] for row in await cursor.fetchall()}
        assert "scope_id" in cols
        assert "taint" in cols

        # Verify old message can still be leased
        leased = await s.lease("proxy_queue")
        assert leased is not None
        assert leased.id == "old-msg"
        assert leased.scope_id is None
        assert leased.taint is None
        assert leased.urgency == "informational"

    async def test_dead_letter_preserves_new_fields(
        self, store: DurableQueueStore
    ) -> None:
        """scope_id and taint are carried into dead_letters table."""
        msg = QueueMessage(
            message_kind="user_message",
            sender="user",
            queue_name="proxy_queue",
            scope_id="scope-dl",
            taint=TaintLevel.owner,
        )
        await store.enqueue(msg)
        await store.lease("proxy_queue")
        await store.dead_letter(msg.id, "test reason")

        async with aiosqlite.connect(store.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT scope_id, taint FROM dead_letters WHERE id = ?", (msg.id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["scope_id"] == "scope-dl"
            assert row["taint"] == "owner"
