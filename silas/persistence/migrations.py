"""Sequential, idempotent migration runner with SHA-256 checksums."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_migrations_sync(db_path: str, migrations_dir: Path) -> None:
    """Synchronous migration runner — avoids aiosqlite's executescript() bug
    where BEGIN/END in trigger bodies gets misinterpreted as transactions."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "  name TEXT PRIMARY KEY,"
            "  checksum TEXT NOT NULL,"
            "  applied_at TEXT NOT NULL"
            ")"
        )
        conn.commit()

        applied = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT name, checksum FROM _migrations ORDER BY name"
            ).fetchall()
        }

        for sql_file in sorted(migrations_dir.glob("*.sql")):
            name = sql_file.name
            checksum = _checksum(sql_file)

            if name in applied:
                if applied[name] != checksum:
                    raise RuntimeError(
                        f"Migration {name} checksum mismatch: "
                        f"applied={applied[name]}, current={checksum}. "
                        f"Previously applied migrations must not be modified."
                    )
                continue

            sql = sql_file.read_text(encoding="utf-8")
            conn.executescript(sql)

            now = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO _migrations (name, checksum, applied_at) VALUES (?, ?, ?)",
                (name, checksum, now),
            )
            conn.commit()
    finally:
        conn.close()


async def run_migrations(db_path: str, migrations_dir: Path | None = None) -> None:
    """Apply all pending migrations in order. Fail-fast on checksum mismatch.

    Uses synchronous sqlite3 internally because aiosqlite.executescript()
    misinterprets BEGIN/END in trigger bodies as transaction boundaries.
    """
    if migrations_dir is None:
        migrations_dir = MIGRATIONS_DIR

    # Run synchronously — migrations are a one-time startup operation,
    # not performance-critical. This avoids the aiosqlite bug cleanly.
    _run_migrations_sync(db_path, migrations_dir)


__all__ = ["run_migrations"]
