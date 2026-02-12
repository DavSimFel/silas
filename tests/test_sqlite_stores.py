"""Tests for SQLite persistence layer (Phase 1b).

Covers: migration runner, memory store, chronicle store,
work item store, audit log, and nonce store.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# These imports will work once Codex builds the stores.
# Tests are written ahead of implementation.

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc(minutes_ago: int = 0) -> datetime:
    return _utc_now() - timedelta(minutes=minutes_ago)


# ---------------------------------------------------------------------------
# Migration Runner
# ---------------------------------------------------------------------------

class TestMigrationRunner:
    async def test_migrations_apply_idempotently(self, tmp_path: Path) -> None:
        """Running migrations twice should not fail."""
        from silas.persistence.migrations import run_migrations
        db_path = tmp_path / "test.db"
        await run_migrations(str(db_path))
        await run_migrations(str(db_path))  # second run = idempotent

    async def test_migrations_create_tables(self, tmp_path: Path) -> None:
        import aiosqlite
        from silas.persistence.migrations import run_migrations
        db_path = tmp_path / "test.db"
        await run_migrations(str(db_path))
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}
        # Should have our core tables
        assert "memories" in tables
        assert "chronicle" in tables
        assert "work_items" in tables
        assert "audit_log" in tables
        assert "nonces" in tables
        assert "_migrations" in tables


# ---------------------------------------------------------------------------
# SQLiteMemoryStore
# ---------------------------------------------------------------------------

class TestSQLiteMemoryStore:
    @pytest.fixture
    async def store(self, tmp_path: Path):
        from silas.memory.sqlite_store import SQLiteMemoryStore
        from silas.persistence.migrations import run_migrations
        db_path = tmp_path / "test.db"
        await run_migrations(str(db_path))
        return SQLiteMemoryStore(str(db_path))

    async def test_store_and_get(self, store) -> None:
        from silas.models.memory import MemoryItem, MemoryType
        from silas.models.messages import TaintLevel
        item = MemoryItem(
            memory_id="m1",
            content="the quick brown fox",
            memory_type=MemoryType.fact,
            taint=TaintLevel.owner,
            source_kind="test",
        )
        result_id = await store.store(item)
        assert result_id == "m1"
        loaded = await store.get("m1")
        assert loaded is not None
        assert loaded.content == "the quick brown fox"
        assert loaded.memory_type == MemoryType.fact

    async def test_get_nonexistent_returns_none(self, store) -> None:
        assert await store.get("nonexistent") is None

    async def test_update(self, store) -> None:
        from silas.models.memory import MemoryItem, MemoryType
        from silas.models.messages import TaintLevel
        item = MemoryItem(
            memory_id="m2",
            content="original",
            memory_type=MemoryType.episode,
            taint=TaintLevel.owner,
            source_kind="test",
        )
        await store.store(item)
        await store.update("m2", content="updated content")
        loaded = await store.get("m2")
        assert loaded is not None
        assert loaded.content == "updated content"

    async def test_delete(self, store) -> None:
        from silas.models.memory import MemoryItem, MemoryType
        from silas.models.messages import TaintLevel
        item = MemoryItem(
            memory_id="m3",
            content="to delete",
            memory_type=MemoryType.fact,
            taint=TaintLevel.owner,
            source_kind="test",
        )
        await store.store(item)
        await store.delete("m3")
        assert await store.get("m3") is None

    async def test_keyword_search_fts5(self, store) -> None:
        from silas.models.memory import MemoryItem, MemoryType
        from silas.models.messages import TaintLevel
        await store.store(MemoryItem(
            memory_id="m4", content="python async programming",
            memory_type=MemoryType.fact, taint=TaintLevel.owner, source_kind="test",
        ))
        await store.store(MemoryItem(
            memory_id="m5", content="rust ownership model",
            memory_type=MemoryType.fact, taint=TaintLevel.owner, source_kind="test",
        ))
        results = await store.search_keyword("python", limit=10)
        assert len(results) >= 1
        assert any(r.memory_id == "m4" for r in results)
        assert not any(r.memory_id == "m5" for r in results)

    async def test_search_keyword_no_results(self, store) -> None:
        results = await store.search_keyword("xyznonexistent", limit=10)
        assert results == []

    async def test_search_session(self, store) -> None:
        from silas.models.memory import MemoryItem, MemoryType
        from silas.models.messages import TaintLevel
        await store.store(MemoryItem(
            memory_id="m6", content="session data",
            memory_type=MemoryType.episode, taint=TaintLevel.owner,
            source_kind="test", session_id="sess-1",
        ))
        await store.store(MemoryItem(
            memory_id="m7", content="other session",
            memory_type=MemoryType.episode, taint=TaintLevel.owner,
            source_kind="test", session_id="sess-2",
        ))
        results = await store.search_session("sess-1")
        assert len(results) == 1
        assert results[0].memory_id == "m6"

    async def test_search_by_type_filters_results(self, store) -> None:
        from silas.models.memory import MemoryItem, MemoryType
        from silas.models.messages import TaintLevel
        await store.store(MemoryItem(
            memory_id="m-type-1", content="entity one",
            memory_type=MemoryType.entity, taint=TaintLevel.owner, source_kind="test",
        ))
        await store.store(MemoryItem(
            memory_id="m-type-2", content="fact one",
            memory_type=MemoryType.fact, taint=TaintLevel.owner, source_kind="test",
        ))
        await store.store(MemoryItem(
            memory_id="m-type-3", content="entity two",
            memory_type=MemoryType.entity, taint=TaintLevel.owner, source_kind="test",
        ))

        results = await store.search_by_type(MemoryType.entity, limit=10)
        assert {item.memory_id for item in results} == {"m-type-1", "m-type-3"}
        assert all(item.memory_type == MemoryType.entity for item in results)

    async def test_list_recent_orders_by_updated_at_desc(self, store) -> None:
        from silas.models.memory import MemoryItem, MemoryType
        from silas.models.messages import TaintLevel
        now = _utc_now()
        await store.store(MemoryItem(
            memory_id="m-recent-1", content="oldest",
            memory_type=MemoryType.fact, taint=TaintLevel.owner,
            source_kind="test",
            created_at=now - timedelta(minutes=3),
            updated_at=now - timedelta(minutes=3),
        ))
        await store.store(MemoryItem(
            memory_id="m-recent-2", content="middle",
            memory_type=MemoryType.fact, taint=TaintLevel.owner,
            source_kind="test",
            created_at=now - timedelta(minutes=2),
            updated_at=now - timedelta(minutes=2),
        ))
        await store.store(MemoryItem(
            memory_id="m-recent-3", content="newest",
            memory_type=MemoryType.fact, taint=TaintLevel.owner,
            source_kind="test",
            created_at=now - timedelta(minutes=1),
            updated_at=now - timedelta(minutes=1),
        ))

        results = await store.list_recent(limit=3)
        assert [item.memory_id for item in results] == [
            "m-recent-3",
            "m-recent-2",
            "m-recent-1",
        ]

    async def test_increment_access_updates_count_and_last_accessed(self, store) -> None:
        from silas.models.memory import MemoryItem, MemoryType
        from silas.models.messages import TaintLevel
        item = MemoryItem(
            memory_id="m-access-1",
            content="track access",
            memory_type=MemoryType.fact,
            taint=TaintLevel.owner,
            source_kind="test",
            access_count=2,
            last_accessed=None,
        )
        await store.store(item)

        before = await store.get("m-access-1")
        assert before is not None
        assert before.access_count == 2
        assert before.last_accessed is None

        await store.increment_access("m-access-1")

        after = await store.get("m-access-1")
        assert after is not None
        assert after.access_count == 3
        assert after.last_accessed is not None
        assert after.updated_at >= before.updated_at

    async def test_store_raw_and_search_raw(self, store) -> None:
        from silas.models.memory import MemoryItem, MemoryType, ReingestionTier
        from silas.models.messages import TaintLevel
        item = MemoryItem(
            memory_id="raw1", content="raw conversation log",
            memory_type=MemoryType.episode, taint=TaintLevel.owner,
            source_kind="conversation_raw",
            reingestion_tier=ReingestionTier.low_reingestion,
        )
        await store.store_raw(item)
        results = await store.search_raw("conversation", limit=5)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# SQLiteChronicleStore
# ---------------------------------------------------------------------------

class TestSQLiteChronicleStore:
    @pytest.fixture
    async def store(self, tmp_path: Path):
        from silas.persistence.chronicle_store import SQLiteChronicleStore
        from silas.persistence.migrations import run_migrations
        db_path = tmp_path / "test.db"
        await run_migrations(str(db_path))
        return SQLiteChronicleStore(str(db_path))

    def _make_item(self, turn: int, content: str = "msg") -> object:
        from silas.models.context import ContextItem, ContextZone
        from silas.models.messages import TaintLevel
        return ContextItem(
            ctx_id=f"c-{turn}",
            zone=ContextZone.chronicle,
            content=content,
            token_count=len(content),
            turn_number=turn,
            source="test",
            taint=TaintLevel.owner,
            kind="message",
        )

    async def test_append_and_get_recent(self, store) -> None:
        await store.append("owner", self._make_item(1, "first"))
        await store.append("owner", self._make_item(2, "second"))
        await store.append("owner", self._make_item(3, "third"))
        recent = await store.get_recent("owner", limit=2)
        assert len(recent) == 2
        # Most recent first or last depends on impl — just check count
        contents = {item.content for item in recent}
        assert "third" in contents
        assert "second" in contents

    async def test_scope_isolation(self, store) -> None:
        await store.append("scope-a", self._make_item(1, "a-msg"))
        await store.append("scope-b", self._make_item(1, "b-msg"))
        a_items = await store.get_recent("scope-a", limit=10)
        assert len(a_items) == 1
        assert a_items[0].content == "a-msg"

    async def test_prune_before(self, store) -> None:
        await store.append("owner", self._make_item(1, "old"))
        cutoff = _utc_now() + timedelta(minutes=1)
        pruned = await store.prune_before(cutoff)
        assert pruned >= 1
        remaining = await store.get_recent("owner", limit=10)
        assert len(remaining) == 0


# ---------------------------------------------------------------------------
# SQLiteWorkItemStore
# ---------------------------------------------------------------------------

class TestSQLiteWorkItemStore:
    @pytest.fixture
    async def store(self, tmp_path: Path):
        from silas.persistence.migrations import run_migrations
        from silas.persistence.work_item_store import SQLiteWorkItemStore
        db_path = tmp_path / "test.db"
        await run_migrations(str(db_path))
        return SQLiteWorkItemStore(str(db_path))

    def _make_item(self, id: str = "wi-1", **kwargs) -> object:
        from silas.models.work import WorkItem, WorkItemType
        defaults = {"id": id, "type": WorkItemType.task, "title": "Test", "body": "Do it"}
        defaults.update(kwargs)
        return WorkItem(**defaults)

    async def test_save_and_get(self, store) -> None:
        wi = self._make_item()
        await store.save(wi)
        loaded = await store.get("wi-1")
        assert loaded is not None
        assert loaded.id == "wi-1"
        assert loaded.title == "Test"

    async def test_get_nonexistent(self, store) -> None:
        assert await store.get("nope") is None

    async def test_list_by_status(self, store) -> None:
        from silas.models.work import WorkItemStatus
        await store.save(self._make_item("wi-1"))
        await store.save(self._make_item("wi-2"))
        results = await store.list_by_status(WorkItemStatus.pending)
        assert len(results) == 2

    async def test_list_by_parent(self, store) -> None:
        await store.save(self._make_item("child-1", parent="parent-1"))
        await store.save(self._make_item("child-2", parent="parent-1"))
        await store.save(self._make_item("other", parent="parent-2"))
        children = await store.list_by_parent("parent-1")
        assert len(children) == 2

    async def test_update_status(self, store) -> None:
        from silas.models.work import BudgetUsed, WorkItemStatus
        await store.save(self._make_item("wi-u"))
        used = BudgetUsed(tokens=500, attempts=2)
        await store.update_status("wi-u", WorkItemStatus.running, used)
        loaded = await store.get("wi-u")
        assert loaded is not None
        assert loaded.status == WorkItemStatus.running
        assert loaded.budget_used.tokens == 500
        assert loaded.budget_used.attempts == 2

    async def test_approval_token_roundtrip(self, store) -> None:
        """ApprovalToken with Base64Bytes signature must survive SQLite roundtrip."""
        from silas.models.approval import ApprovalScope, ApprovalToken, ApprovalVerdict
        token = ApprovalToken(
            token_id="tok-1",
            plan_hash="hash123",
            work_item_id="wi-tok",
            scope=ApprovalScope.full_plan,
            verdict=ApprovalVerdict.approved,
            signature=b"\x00\xff\xde\xad",
            expires_at=_utc_now() + timedelta(minutes=30),
            nonce="n1",
        )
        wi = self._make_item("wi-tok")
        wi.approval_token = token
        await store.save(wi)
        loaded = await store.get("wi-tok")
        assert loaded is not None
        assert loaded.approval_token is not None
        assert loaded.approval_token.signature == b"\x00\xff\xde\xad"
        assert loaded.approval_token.token_id == "tok-1"


# ---------------------------------------------------------------------------
# SQLiteAuditLog
# ---------------------------------------------------------------------------

class TestSQLiteAuditLog:
    @pytest.fixture
    async def audit(self, tmp_path: Path):
        from silas.audit.sqlite_audit import SQLiteAuditLog
        from silas.persistence.migrations import run_migrations
        db_path = tmp_path / "test.db"
        await run_migrations(str(db_path))
        return SQLiteAuditLog(str(db_path))

    async def test_log_and_verify_chain(self, audit) -> None:
        await audit.log("event_a", key="value1")
        await audit.log("event_b", key="value2")
        await audit.log("event_c", key="value3")
        valid, count = await audit.verify_chain()
        assert valid is True
        assert count == 3

    async def test_empty_chain_is_valid(self, audit) -> None:
        valid, count = await audit.verify_chain()
        assert valid is True
        assert count == 0

    async def test_checkpoint_and_verify_from(self, audit) -> None:
        await audit.log("before_checkpoint")
        cp_id = await audit.write_checkpoint()
        await audit.log("after_checkpoint")
        valid, count = await audit.verify_from_checkpoint(cp_id)
        assert valid is True
        assert count >= 1


# ---------------------------------------------------------------------------
# SQLiteNonceStore
# ---------------------------------------------------------------------------

class TestSQLiteNonceStore:
    @pytest.fixture
    async def nonce_store(self, tmp_path: Path):
        from silas.persistence.migrations import run_migrations
        from silas.persistence.nonce_store import SQLiteNonceStore
        db_path = tmp_path / "test.db"
        await run_migrations(str(db_path))
        return SQLiteNonceStore(str(db_path))

    async def test_record_and_check(self, nonce_store) -> None:
        assert await nonce_store.is_used("msg", "n1") is False
        await nonce_store.record("msg", "n1")
        assert await nonce_store.is_used("msg", "n1") is True

    async def test_domain_isolation(self, nonce_store) -> None:
        """Nonces in different domains don't collide."""
        await nonce_store.record("msg", "shared-nonce")
        assert await nonce_store.is_used("msg", "shared-nonce") is True
        assert await nonce_store.is_used("exec", "shared-nonce") is False

    async def test_prune_expired(self, nonce_store) -> None:
        await nonce_store.record("msg", "old-nonce")
        # Prune everything older than future = should remove it
        pruned = await nonce_store.prune_expired(_utc_now() + timedelta(hours=1))
        assert pruned >= 1
        assert await nonce_store.is_used("msg", "old-nonce") is False
