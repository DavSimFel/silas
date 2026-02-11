"""SQLite persona persistence for personality state and event history."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite

from silas.models.personality import PersonaEvent, PersonaState


class SQLitePersonaStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def get_state(self, scope_id: str) -> PersonaState | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM persona_state WHERE scope_id = ?",
                (scope_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return _row_to_state(row)

    async def save_state(self, state: PersonaState) -> None:
        data = state.model_dump(mode="json")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO persona_state (
                    scope_id, baseline_axes, mood, active_preset,
                    voice, last_context, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["scope_id"],
                    json.dumps(data["baseline_axes"]),
                    json.dumps(data["mood"]),
                    data["active_preset"],
                    json.dumps(data["voice"]),
                    data["last_context"],
                    data["updated_at"],
                ),
            )
            await db.commit()

    async def append_event(self, event: PersonaEvent) -> None:
        data = event.model_dump(mode="json")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO persona_events (
                    event_id, scope_id, event_type, trusted,
                    delta_axes, delta_mood, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["event_id"],
                    data["scope_id"],
                    data["event_type"],
                    1 if data["trusted"] else 0,
                    json.dumps(data["delta_axes"]),
                    json.dumps(data["delta_mood"]),
                    data["source"],
                    data["created_at"],
                ),
            )
            await db.commit()

    async def list_events(self, scope_id: str, limit: int = 100) -> list[PersonaEvent]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM persona_events
                   WHERE scope_id = ?
                   ORDER BY created_at DESC, id DESC
                   LIMIT ?""",
                (scope_id, limit),
            )
            rows = await cursor.fetchall()
            return [_row_to_event(row) for row in rows]


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _row_to_state(row: aiosqlite.Row) -> PersonaState:
    return PersonaState(
        scope_id=row["scope_id"],
        baseline_axes=json.loads(row["baseline_axes"]),
        mood=json.loads(row["mood"]),
        active_preset=row["active_preset"],
        voice=json.loads(row["voice"]),
        last_context=row["last_context"],
        updated_at=_parse_dt(row["updated_at"]),
    )


def _row_to_event(row: aiosqlite.Row) -> PersonaEvent:
    return PersonaEvent(
        event_id=row["event_id"],
        scope_id=row["scope_id"],
        event_type=row["event_type"],
        trusted=bool(row["trusted"]),
        delta_axes=json.loads(row["delta_axes"]),
        delta_mood=json.loads(row["delta_mood"]),
        source=row["source"],
        created_at=_parse_dt(row["created_at"]),
    )


__all__ = ["SQLitePersonaStore"]
