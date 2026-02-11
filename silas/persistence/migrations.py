"""Sequential, idempotent migration runner with SHA-256 checksums."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "memory" / "migrations"


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def run_migrations(db_path: str, migrations_dir: Path | None = None) -> None:
    """Apply all pending migrations in order. Fail-fast on checksum mismatch."""
    if migrations_dir is None:
        migrations_dir = MIGRATIONS_DIR

    async with aiosqlite.connect(db_path) as db:
        # Ensure _migrations table exists
        await db.execute(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "  name TEXT PRIMARY KEY,"
            "  checksum TEXT NOT NULL,"
            "  applied_at TEXT NOT NULL"
            ")"
        )
        await db.commit()

        # Get already applied migrations
        cursor = await db.execute("SELECT name, checksum FROM _migrations ORDER BY name")
        applied = {row[0]: row[1] for row in await cursor.fetchall()}

        # Find and sort migration files
        sql_files = sorted(migrations_dir.glob("*.sql"))

        for sql_file in sql_files:
            name = sql_file.name
            checksum = _checksum(sql_file)

            if name in applied:
                # Verify checksum hasn't changed
                if applied[name] != checksum:
                    raise RuntimeError(
                        f"Migration {name} checksum mismatch: "
                        f"applied={applied[name]}, current={checksum}. "
                        f"Previously applied migrations must not be modified."
                    )
                continue

            # Apply migration
            sql = sql_file.read_text(encoding="utf-8")
            await db.executescript(sql)

            # Record it (the _migrations CREATE in the SQL is idempotent,
            # but we still INSERT only from here)
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT OR IGNORE INTO _migrations (name, checksum, applied_at) VALUES (?, ?, ?)",
                (name, checksum, now),
            )
            await db.commit()


__all__ = ["run_migrations"]
