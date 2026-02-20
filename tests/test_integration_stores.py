"""Integration tests for SQLite persistence stores."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
from silas.context.sqlite_store import SQLiteMemoryStore
from silas.models.context import ContextItem, ContextZone
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import TaintLevel
from silas.models.work import BudgetUsed, WorkItem, WorkItemStatus, WorkItemType
from silas.persistence.audit import SQLiteAuditLog
from silas.persistence.chronicle_store import SQLiteChronicleStore
from silas.persistence.migrations import run_migrations
from silas.persistence.nonce_store import SQLiteNonceStore
from silas.persistence.work_item_store import SQLiteWorkItemStore

pytestmark = pytest.mark.asyncio


def _now() -> datetime:
    return datetime.now(UTC)


def _memory(
    memory_id: str,
    content: str,
    *,
    session_id: str | None = None,
    source_kind: str = "integration_test",
) -> MemoryItem:
    return MemoryItem(
        memory_id=memory_id,
        content=content,
        memory_type=MemoryType.fact,
        taint=TaintLevel.owner,
        session_id=session_id,
        source_kind=source_kind,
    )


def _context_item(
    ctx_id: str,
    turn_number: int,
    content: str,
    *,
    created_at: datetime | None = None,
) -> ContextItem:
    return ContextItem(
        ctx_id=ctx_id,
        zone=ContextZone.chronicle,
        content=content,
        token_count=len(content),
        created_at=created_at or _now(),
        turn_number=turn_number,
        source="integration_test",
        taint=TaintLevel.owner,
        kind="message",
    )


def _work_item(item_id: str, *, status: WorkItemStatus = WorkItemStatus.pending) -> WorkItem:
    return WorkItem(
        id=item_id,
        type=WorkItemType.task,
        title=f"Work item {item_id}",
        body="Execute integration flow",
        status=status,
    )


@pytest.fixture
async def db_path(tmp_path: Path) -> str:
    db = tmp_path / "integration.db"
    await run_migrations(str(db))
    return str(db)


async def test_memory_store_crud_round_trip(db_path: str) -> None:
    store = SQLiteMemoryStore(db_path)

    memory = _memory("mem-crud", "initial memory content", session_id="session-a")
    created_id = await store.store(memory)
    assert created_id == "mem-crud"

    loaded = await store.get("mem-crud")
    assert loaded is not None
    assert loaded.content == "initial memory content"
    assert loaded.session_id == "session-a"

    await store.update("mem-crud", content="updated memory content", session_id="session-b")

    updated = await store.get("mem-crud")
    assert updated is not None
    assert updated.content == "updated memory content"
    assert updated.session_id == "session-b"

    await store.delete("mem-crud")
    assert await store.get("mem-crud") is None


async def test_memory_store_fts5_search_returns_expected_rows(db_path: str) -> None:
    store = SQLiteMemoryStore(db_path)

    await store.store(_memory("mem-fts-1", "python asyncio event loop"))
    await store.store(_memory("mem-fts-2", "sqlite persistence layer"))
    await store.store(_memory("mem-fts-3", "distributed systems design"))

    results = await store.search_keyword("sqlite", limit=10)
    assert {item.memory_id for item in results} == {"mem-fts-2"}


async def test_memory_store_session_search_isolated_by_session_id(db_path: str) -> None:
    store = SQLiteMemoryStore(db_path)

    await store.store(_memory("mem-sess-1", "session alpha item one", session_id="session-alpha"))
    await store.store(_memory("mem-sess-2", "session alpha item two", session_id="session-alpha"))
    await store.store(_memory("mem-sess-3", "session beta item", session_id="session-beta"))

    alpha_results = await store.search_session("session-alpha")
    assert {item.memory_id for item in alpha_results} == {"mem-sess-1", "mem-sess-2"}
    assert all(item.session_id == "session-alpha" for item in alpha_results)


async def test_memory_store_search_by_type_and_recent(db_path: str) -> None:
    store = SQLiteMemoryStore(db_path)

    now = _now()
    await store.store(
        MemoryItem(
            memory_id="mem-type-entity",
            content="entity memory row",
            memory_type=MemoryType.entity,
            taint=TaintLevel.owner,
            source_kind="integration_test",
            created_at=now - timedelta(minutes=3),
            updated_at=now - timedelta(minutes=3),
        )
    )
    await store.store(
        MemoryItem(
            memory_id="mem-type-fact",
            content="fact memory row",
            memory_type=MemoryType.fact,
            taint=TaintLevel.owner,
            source_kind="integration_test",
            created_at=now - timedelta(minutes=1),
            updated_at=now - timedelta(minutes=1),
        )
    )

    entities = await store.search_by_type(MemoryType.entity, limit=10)
    assert [item.memory_id for item in entities] == ["mem-type-entity"]

    recent = await store.list_recent(limit=2)
    assert [item.memory_id for item in recent] == ["mem-type-fact", "mem-type-entity"]


async def test_memory_store_increment_access_updates_fields(db_path: str) -> None:
    store = SQLiteMemoryStore(db_path)

    await store.store(_memory("mem-access", "track access updates"))
    before = await store.get("mem-access")
    assert before is not None
    assert before.access_count == 0
    assert before.last_accessed is None

    await store.increment_access("mem-access")

    after = await store.get("mem-access")
    assert after is not None
    assert after.access_count == 1
    assert after.last_accessed is not None
    assert after.updated_at >= before.updated_at


async def test_chronicle_store_round_trip_with_scope_isolation(db_path: str) -> None:
    store = SQLiteChronicleStore(db_path)

    await store.append("scope-a", _context_item("scope-a-1", 1, "a first"))
    await store.append("scope-a", _context_item("scope-a-2", 2, "a second"))
    await store.append("scope-b", _context_item("scope-b-1", 1, "b first"))

    scope_a_items = await store.get_recent("scope-a", limit=10)
    scope_b_items = await store.get_recent("scope-b", limit=10)

    assert [item.ctx_id for item in scope_a_items] == ["scope-a-1", "scope-a-2"]
    assert [item.ctx_id for item in scope_b_items] == ["scope-b-1"]


async def test_chronicle_store_prune_before_keeps_only_recent_rows(db_path: str) -> None:
    store = SQLiteChronicleStore(db_path)
    now = _now()

    for i in range(10):
        await store.append(
            "scope-prune",
            _context_item(
                f"ctx-prune-{i}",
                turn_number=i + 1,
                content=f"entry {i}",
                created_at=now - timedelta(minutes=10 - i),
            ),
        )

    cutoff = now - timedelta(minutes=5)
    pruned = await store.prune_before(cutoff)
    assert pruned == 5

    remaining = await store.get_recent("scope-prune", limit=20)
    assert {item.ctx_id for item in remaining} == {
        "ctx-prune-5",
        "ctx-prune-6",
        "ctx-prune-7",
        "ctx-prune-8",
        "ctx-prune-9",
    }


async def test_work_item_store_round_trip_and_status_update(db_path: str) -> None:
    store = SQLiteWorkItemStore(db_path)

    work_item = _work_item("wi-int-1")
    await store.save(work_item)

    loaded = await store.get("wi-int-1")
    assert loaded is not None
    assert loaded.id == "wi-int-1"
    assert loaded.status == WorkItemStatus.pending

    pending = await store.list_by_status(WorkItemStatus.pending)
    assert any(item.id == "wi-int-1" for item in pending)

    budget_used = BudgetUsed(tokens=321, attempts=1)
    await store.update_status("wi-int-1", WorkItemStatus.running, budget_used)

    updated = await store.get("wi-int-1")
    assert updated is not None
    assert updated.status == WorkItemStatus.running
    assert updated.budget_used.tokens == 321
    assert updated.budget_used.attempts == 1

    running = await store.list_by_status(WorkItemStatus.running)
    assert any(item.id == "wi-int-1" for item in running)


async def test_audit_log_hash_chain_and_checkpoint_verification(db_path: str) -> None:
    audit = SQLiteAuditLog(db_path)

    for i in range(5):
        await audit.log(f"event_{i}", sequence=i)

    valid, count = await audit.verify_chain()
    assert valid is True
    assert count == 5

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT entry_id, event, data, timestamp, prev_hash, entry_hash "
            "FROM audit_log ORDER BY id ASC"
        )
        rows = await cursor.fetchall()

    assert len(rows) == 5

    expected_prev = "genesis"
    for row in rows:
        assert row["prev_hash"] == expected_prev

        canonical = json.dumps(
            {
                "entry_id": row["entry_id"],
                "event": row["event"],
                "data": row["data"],
                "timestamp": row["timestamp"],
                "prev_hash": row["prev_hash"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == row["entry_hash"]
        expected_prev = row["entry_hash"]

    checkpoint_id = await audit.write_checkpoint()
    cp_valid, cp_count = await audit.verify_from_checkpoint(checkpoint_id)
    assert cp_valid is True
    assert cp_count >= 1


async def test_audit_log_tamper_detection_from_checkpoint(db_path: str) -> None:
    audit = SQLiteAuditLog(db_path)

    await audit.log("event_alpha", sequence=1)
    await audit.log("event_beta", sequence=2)
    checkpoint_id = await audit.write_checkpoint()
    await audit.log("event_gamma", sequence=3)

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE audit_log SET data = ? WHERE event = ?",
            ('{"sequence":999,"tampered":true}', "event_gamma"),
        )
        await db.commit()

    valid, count = await audit.verify_from_checkpoint(checkpoint_id)
    assert valid is False
    assert count == 0


async def test_nonce_store_replay_protection(db_path: str) -> None:
    store = SQLiteNonceStore(db_path)

    await store.record("message", "nonce-123")
    assert await store.is_used("message", "nonce-123") is True
    assert await store.is_used("message", "nonce-456") is False


async def test_nonce_store_ttl_pruning_removes_only_expired_entries(db_path: str) -> None:
    store = SQLiteNonceStore(db_path)
    now = _now()

    old_key = "message:nonce-old"
    fresh_key = "message:nonce-fresh"

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO nonces (key, domain, nonce, recorded_at) VALUES (?, ?, ?, ?)",
            (old_key, "message", "nonce-old", (now - timedelta(days=2)).isoformat()),
        )
        await db.execute(
            "INSERT INTO nonces (key, domain, nonce, recorded_at) VALUES (?, ?, ?, ?)",
            (fresh_key, "message", "nonce-fresh", (now - timedelta(minutes=5)).isoformat()),
        )
        await db.commit()

    pruned = await store.prune_expired(now - timedelta(hours=1))
    assert pruned == 1

    assert await store.is_used("message", "nonce-old") is False
    assert await store.is_used("message", "nonce-fresh") is True
