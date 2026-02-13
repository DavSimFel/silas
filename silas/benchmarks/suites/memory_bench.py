"""Memory store benchmarks — read/write throughput for SQLite FTS5 store.

Why benchmark memory: memory retrieval is on the critical path of every
turn that uses context enrichment. Write throughput matters for episode
ingestion after each turn.
"""

from __future__ import annotations

import tempfile

from silas.benchmarks.runner import benchmark
from silas.memory.sqlite_store import SQLiteMemoryStore
from silas.models.memory import MemoryItem, MemoryType


def _make_item(i: int) -> MemoryItem:
    return MemoryItem(
        memory_id=f"bench_{i}",
        content=f"Benchmark memory item number {i} with some searchable content about topic {i % 10}",
        memory_type=MemoryType.episode,
    )


async def _fresh_store() -> SQLiteMemoryStore:
    """Create an ephemeral SQLite memory store with FTS5 tables."""
    tmpdir = tempfile.mkdtemp()
    db_path = f"{tmpdir}/bench.db"
    store = SQLiteMemoryStore(db_path)
    # Initialize tables — mirrors test fixtures
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                reingestion_tier TEXT NOT NULL DEFAULT 'active',
                trust_level TEXT NOT NULL DEFAULT 'working',
                taint TEXT NOT NULL DEFAULT 'owner',
                created_at TEXT NOT NULL,
                updated_at TEXT,
                valid_from TEXT,
                valid_until TEXT,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed TEXT,
                semantic_tags TEXT NOT NULL DEFAULT '[]',
                entity_refs TEXT NOT NULL DEFAULT '[]',
                causal_refs TEXT NOT NULL DEFAULT '[]',
                temporal_next TEXT,
                temporal_prev TEXT,
                session_id TEXT,
                embedding BLOB,
                source_kind TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content, memory_type, semantic_tags, entity_refs,
                content_rowid='rowid'
            );
            """
        )
    return store


@benchmark(name="memory.write", tags=["memory", "write"], iterations=20)
async def bench_memory_write() -> None:
    """Measure write throughput — 50 items per iteration."""
    store = await _fresh_store()
    for i in range(50):
        await store.store(_make_item(i))


@benchmark(name="memory.read_keyword", tags=["memory", "read"], iterations=20)
async def bench_memory_read() -> None:
    """Measure keyword search after pre-filling 100 items."""
    store = await _fresh_store()
    for i in range(100):
        await store.store(_make_item(i))
    for _ in range(10):
        await store.search_keyword("topic", limit=10)
