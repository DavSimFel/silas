"""SQLite persistence for service connections."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite

from silas.models.connections import Connection


class SQLiteConnectionStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def get_connection(self, connection_id: str) -> Connection | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM connections WHERE connection_id = ?",
                (connection_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return _row_to_connection(row)

    async def save_connection(self, connection: Connection) -> None:
        data = connection.model_dump(mode="json")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO connections (
                    connection_id, skill_name, provider, status, permissions_granted,
                    token_expires_at, last_refresh, last_health_check, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["connection_id"],
                    data["skill_name"],
                    data["provider"],
                    data["status"],
                    json.dumps(data["permissions_granted"]),
                    data["token_expires_at"],
                    data["last_refresh"],
                    data["last_health_check"],
                    data["created_at"],
                    data["updated_at"],
                ),
            )
            await db.commit()

    async def list_connections(self, domain: str | None = None) -> list[Connection]:
        query = "SELECT * FROM connections"
        params: tuple[object, ...] = ()

        if domain is not None:
            query += " WHERE skill_name LIKE ? OR provider LIKE ? OR connection_id LIKE ?"
            match = f"%{domain}%"
            params = (match, match, match)

        query += " ORDER BY connection_id"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [_row_to_connection(row) for row in rows]

    async def delete_connection(self, connection_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM connections WHERE connection_id = ?",
                (connection_id,),
            )
            await db.commit()
            return cursor.rowcount > 0


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None

    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _row_to_connection(row: aiosqlite.Row) -> Connection:
    data = {
        "connection_id": row["connection_id"],
        "skill_name": row["skill_name"],
        "provider": row["provider"],
        "status": row["status"],
        "permissions_granted": json.loads(row["permissions_granted"]),
        "token_expires_at": _parse_dt(row["token_expires_at"]),
        "last_refresh": _parse_dt(row["last_refresh"]),
        "last_health_check": _parse_dt(row["last_health_check"]),
        "created_at": _parse_dt(row["created_at"]),
        "updated_at": _parse_dt(row["updated_at"]),
    }
    return Connection.model_validate(data)


__all__ = ["SQLiteConnectionStore"]
