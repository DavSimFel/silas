"""Hash-chained SQLite audit log."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import aiosqlite


class SQLiteAuditLog:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def log(self, event: str, **data: object) -> str:
        entry_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        data_json = json.dumps(data, default=str, sort_keys=True)

        async with aiosqlite.connect(self.db_path) as db:
            # Get previous entry hash
            cursor = await db.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")
            row = await cursor.fetchone()
            prev_hash = row[0] if row else "genesis"

            # Compute this entry's hash
            canonical = json.dumps(
                {
                    "entry_id": entry_id,
                    "event": event,
                    "data": data_json,
                    "timestamp": now,
                    "prev_hash": prev_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            entry_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

            await db.execute(
                """INSERT INTO audit_log (entry_id, event, data, timestamp, prev_hash, entry_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entry_id, event, data_json, now, prev_hash, entry_hash),
            )
            await db.commit()
        return entry_id

    async def verify_chain(self) -> tuple[bool, int]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM audit_log ORDER BY id ASC")
            rows = await cursor.fetchall()

        if not rows:
            return True, 0

        expected_prev = "genesis"
        for row in rows:
            # Verify prev_hash linkage
            if row["prev_hash"] != expected_prev:
                return False, 0

            # Verify entry_hash
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
            computed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if computed != row["entry_hash"]:
                return False, 0

            expected_prev = row["entry_hash"]

        return True, len(rows)

    async def write_checkpoint(self) -> str:
        checkpoint_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT entry_id, entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if row is None:
                entry_id, entry_hash = "empty", "empty"
            else:
                entry_id, entry_hash = row[0], row[1]

            await db.execute(
                """INSERT INTO audit_checkpoints (checkpoint_id, entry_id, entry_hash, created_at)
                   VALUES (?, ?, ?, ?)""",
                (checkpoint_id, entry_id, entry_hash, now),
            )
            await db.commit()
        return checkpoint_id

    async def verify_from_checkpoint(self, checkpoint_id: str | None = None) -> tuple[bool, int]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            if checkpoint_id is None:
                cursor = await db.execute(
                    "SELECT * FROM audit_checkpoints ORDER BY created_at DESC LIMIT 1"
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM audit_checkpoints WHERE checkpoint_id = ?",
                    (checkpoint_id,),
                )
            cp = await cursor.fetchone()

            if cp is None:
                return await self.verify_chain()

            cp_entry_hash = cp["entry_hash"]
            if cp_entry_hash == "empty":
                # Checkpoint at empty — verify full chain
                return await self.verify_chain()

            # Find the checkpoint entry's row id
            cursor = await db.execute(
                "SELECT id FROM audit_log WHERE entry_id = ?", (cp["entry_id"],)
            )
            cp_row = await cursor.fetchone()
            if cp_row is None:
                return False, 0

            # Verify from checkpoint entry onwards
            cursor = await db.execute(
                "SELECT * FROM audit_log WHERE id >= ? ORDER BY id ASC",
                (cp_row["id"],),
            )
            rows = await cursor.fetchall()

        if not rows:
            return True, 0

        # First entry should match checkpoint hash
        if rows[0]["entry_hash"] != cp_entry_hash:
            return False, 0

        expected_prev = rows[0]["prev_hash"]
        for row in rows:
            if row["prev_hash"] != expected_prev:
                return False, 0
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
            computed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if computed != row["entry_hash"]:
                return False, 0
            expected_prev = row["entry_hash"]

        return True, len(rows)


__all__ = ["SQLiteAuditLog"]
