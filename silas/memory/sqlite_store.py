"""SQLite + FTS5 implementation of MemoryStore."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite

from silas.models.memory import MemoryItem, MemoryType, ReingestionTier, TrustLevel
from silas.models.messages import TaintLevel, utc_now


class SQLiteMemoryStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def store(self, item: MemoryItem) -> str:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO memories (
                    memory_id, content, memory_type, reingestion_tier, trust_level,
                    taint, created_at, updated_at, valid_from, valid_until,
                    access_count, last_accessed, semantic_tags, entity_refs,
                    causal_refs, temporal_next, temporal_prev, session_id,
                    embedding, source_kind
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.memory_id,
                    item.content,
                    item.memory_type.value,
                    item.reingestion_tier.value,
                    item.trust_level.value,
                    item.taint.value,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                    item.valid_from.isoformat() if item.valid_from else None,
                    item.valid_until.isoformat() if item.valid_until else None,
                    item.access_count,
                    item.last_accessed.isoformat() if item.last_accessed else None,
                    json.dumps(item.semantic_tags),
                    json.dumps(item.entity_refs),
                    json.dumps(item.causal_refs),
                    item.temporal_next,
                    item.temporal_prev,
                    item.session_id,
                    None,  # embedding reserved for Phase 8
                    item.source_kind,
                ),
            )
            await db.commit()
        return item.memory_id

    async def get(self, memory_id: str) -> MemoryItem | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM memories WHERE memory_id = ?", (memory_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return _row_to_item(row)

    async def update(self, memory_id: str, **kwargs: object) -> None:
        item = await self.get(memory_id)
        if item is None:
            return
        data = item.model_dump(mode="python")
        data.update(kwargs)
        data["updated_at"] = utc_now()
        updated = MemoryItem.model_validate(data)
        await self.store(updated)

    async def delete(self, memory_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
            await db.commit()

    async def search_keyword(
        self, query: str, limit: int, *, session_id: str | None = None
    ) -> list[MemoryItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # FTS5 query — escape special chars
            safe_query = query.replace('"', '""')
            if session_id is not None:
                cursor = await db.execute(
                    """SELECT m.* FROM memories m
                       JOIN memories_fts fts ON m.rowid = fts.rowid
                       WHERE memories_fts MATCH ?
                       AND m.session_id = ?
                       LIMIT ?""",
                    (f'"{safe_query}"', session_id, limit),
                )
            else:
                cursor = await db.execute(
                    """SELECT m.* FROM memories m
                       JOIN memories_fts fts ON m.rowid = fts.rowid
                       WHERE memories_fts MATCH ?
                       LIMIT ?""",
                    (f'"{safe_query}"', limit),
                )
            rows = await cursor.fetchall()
            return [_row_to_item(r) for r in rows]

    async def search_by_type(
        self, memory_type: MemoryType, limit: int, *, session_id: str | None = None
    ) -> list[MemoryItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if session_id is not None:
                cursor = await db.execute(
                    """SELECT * FROM memories
                       WHERE memory_type = ? AND session_id = ?
                       ORDER BY updated_at DESC, created_at DESC, memory_id ASC
                       LIMIT ?""",
                    (memory_type.value, session_id, limit),
                )
            else:
                cursor = await db.execute(
                    """SELECT * FROM memories
                       WHERE memory_type = ?
                       ORDER BY updated_at DESC, created_at DESC, memory_id ASC
                       LIMIT ?""",
                    (memory_type.value, limit),
                )
            rows = await cursor.fetchall()
            return [_row_to_item(r) for r in rows]

    async def list_recent(self, limit: int) -> list[MemoryItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM memories
                   ORDER BY updated_at DESC, created_at DESC, memory_id ASC
                   LIMIT ?""",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [_row_to_item(r) for r in rows]

    async def increment_access(self, memory_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE memories
                   SET access_count = access_count + 1,
                       last_accessed = ?,
                       updated_at = ?
                   WHERE memory_id = ?""",
                (now, now, memory_id),
            )
            await db.commit()

    async def search_session(self, session_id: str) -> list[MemoryItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM memories
                   WHERE session_id = ?
                   ORDER BY updated_at DESC, created_at DESC, memory_id ASC""",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [_row_to_item(r) for r in rows]

    async def store_raw(self, item: MemoryItem) -> str:
        return await self.store(item)

    async def search_raw(self, query: str, limit: int) -> list[MemoryItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            safe_query = query.replace('"', '""')
            cursor = await db.execute(
                """SELECT m.* FROM memories m
                   JOIN memories_fts fts ON m.rowid = fts.rowid
                   WHERE memories_fts MATCH ?
                   AND m.reingestion_tier = 'low_reingestion'
                   LIMIT ?""",
                (f'"{safe_query}"', limit),
            )
            rows = await cursor.fetchall()
            return [_row_to_item(r) for r in rows]


def _parse_dt(val: str | None):
    if val is None:
        return None
    from datetime import datetime

    dt = datetime.fromisoformat(val)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _row_to_item(row: aiosqlite.Row) -> MemoryItem:
    return MemoryItem(
        memory_id=row["memory_id"],
        content=row["content"],
        memory_type=MemoryType(row["memory_type"]),
        reingestion_tier=ReingestionTier(row["reingestion_tier"]),
        trust_level=TrustLevel(row["trust_level"]),
        taint=TaintLevel(row["taint"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        valid_from=_parse_dt(row["valid_from"]),
        valid_until=_parse_dt(row["valid_until"]),
        access_count=row["access_count"],
        last_accessed=_parse_dt(row["last_accessed"]),
        semantic_tags=json.loads(row["semantic_tags"]),
        entity_refs=json.loads(row["entity_refs"]),
        causal_refs=json.loads(row["causal_refs"]),
        temporal_next=row["temporal_next"],
        temporal_prev=row["temporal_prev"],
        session_id=row["session_id"],
        source_kind=row["source_kind"],
    )


__all__ = ["SQLiteMemoryStore"]
