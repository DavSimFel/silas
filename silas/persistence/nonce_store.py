"""SQLite nonce store for replay protection."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite


class SQLiteNonceStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def is_used(self, domain: str, nonce: str) -> bool:
        key = f"{domain}:{nonce}"
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT 1 FROM nonces WHERE key = ?", (key,))
            return await cursor.fetchone() is not None

    async def record(self, domain: str, nonce: str) -> None:
        key = f"{domain}:{nonce}"
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO nonces (key, domain, nonce, recorded_at) VALUES (?, ?, ?, ?)",
                (key, domain, nonce, now),
            )
            await db.commit()

    async def prune_expired(self, older_than: datetime) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM nonces WHERE recorded_at < ?",
                (older_than.isoformat(),),
            )
            await db.commit()
            return cursor.rowcount


__all__ = ["SQLiteNonceStore"]
