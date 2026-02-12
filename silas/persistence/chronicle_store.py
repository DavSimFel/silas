"""SQLite chronicle persistence for conversation rehydration."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from silas.models.context import ContextItem, ContextZone
from silas.models.messages import TaintLevel


class SQLiteChronicleStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def append(self, scope_id: str, item: ContextItem) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO chronicle (
                    scope_id, ctx_id, zone, content, token_count,
                    created_at, turn_number, source, taint, kind,
                    relevance, masked, pinned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scope_id,
                    item.ctx_id,
                    item.zone.value,
                    item.content,
                    item.token_count,
                    item.created_at.isoformat(),
                    item.turn_number,
                    item.source,
                    item.taint.value,
                    item.kind,
                    item.relevance,
                    1 if item.masked else 0,
                    1 if item.pinned else 0,
                ),
            )
            await db.commit()

    async def get_recent(self, scope_id: str, limit: int) -> list[ContextItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM chronicle
                   WHERE scope_id = ?
                   ORDER BY turn_number DESC, id DESC
                   LIMIT ?""",
                (scope_id, limit),
            )
            rows = await cursor.fetchall()
            return [_row_to_item(r) for r in reversed(rows)]

    async def prune_before(self, cutoff: datetime) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM chronicle WHERE created_at < ?",
                (cutoff.isoformat(),),
            )
            await db.commit()
            return cursor.rowcount


def _parse_dt(val: str):
    from datetime import datetime

    dt = datetime.fromisoformat(val)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _row_to_item(row: aiosqlite.Row) -> ContextItem:
    return ContextItem(
        ctx_id=row["ctx_id"],
        zone=ContextZone(row["zone"]),
        content=row["content"],
        token_count=row["token_count"],
        created_at=_parse_dt(row["created_at"]),
        turn_number=row["turn_number"],
        source=row["source"],
        taint=TaintLevel(row["taint"]),
        kind=row["kind"],
        relevance=row["relevance"],
        masked=bool(row["masked"]),
        pinned=bool(row["pinned"]),
    )


__all__ = ["SQLiteChronicleStore"]
