"""SQLite work item persistence with JSON-serialized complex fields."""

from __future__ import annotations

import json

import aiosqlite

from silas.models.work import BudgetUsed, WorkItem, WorkItemStatus


class SQLiteWorkItemStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def save(self, item: WorkItem) -> None:
        data = item.model_dump(mode="json")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO work_items (
                    id, type, title, parent, spawned_by, follow_up_of, domain,
                    agent, budget, needs_approval, approval_token,
                    body, interaction_mode, input_artifacts_from,
                    verify, gates, skills, access_levels, escalation,
                    schedule, on_failure, on_stuck, failure_context,
                    tasks, depends_on, status, attempts, budget_used,
                    verification_results, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["id"],
                    data["type"],
                    data["title"],
                    data.get("parent"),
                    data.get("spawned_by"),
                    data.get("follow_up_of"),
                    data.get("domain"),
                    data["agent"],
                    json.dumps(data["budget"]),
                    1 if data["needs_approval"] else 0,
                    json.dumps(data["approval_token"]) if data.get("approval_token") else None,
                    data["body"],
                    data["interaction_mode"],
                    json.dumps(data["input_artifacts_from"]),
                    json.dumps(data["verify"]),
                    json.dumps(data["gates"]),
                    json.dumps(data["skills"]),
                    json.dumps(data["access_levels"]),
                    json.dumps(data["escalation"]),
                    data.get("schedule"),
                    data["on_failure"],
                    data["on_stuck"],
                    data.get("failure_context"),
                    json.dumps(data["tasks"]),
                    json.dumps(data["depends_on"]),
                    data["status"],
                    data["attempts"],
                    json.dumps(data["budget_used"]),
                    json.dumps(data["verification_results"]),
                    data["created_at"],
                ),
            )
            await db.commit()

    async def get(self, work_item_id: str) -> WorkItem | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return _row_to_work_item(row)

    async def list_by_status(self, status: WorkItemStatus) -> list[WorkItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM work_items WHERE status = ?", (status.value,))
            rows = await cursor.fetchall()
            return [_row_to_work_item(r) for r in rows]

    async def list_by_parent(self, parent_id: str) -> list[WorkItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM work_items WHERE parent = ?", (parent_id,))
            rows = await cursor.fetchall()
            return [_row_to_work_item(r) for r in rows]

    async def update_status(
        self, work_item_id: str, status: WorkItemStatus, budget_used: BudgetUsed
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE work_items SET status = ?, budget_used = ? WHERE id = ?",
                (status.value, json.dumps(budget_used.model_dump(mode="json")), work_item_id),
            )
            await db.commit()


def _row_to_work_item(row: aiosqlite.Row) -> WorkItem:
    data = {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "parent": row["parent"],
        "spawned_by": row["spawned_by"],
        "follow_up_of": row["follow_up_of"],
        "domain": row["domain"],
        "agent": row["agent"],
        "budget": json.loads(row["budget"]),
        "needs_approval": bool(row["needs_approval"]),
        "approval_token": json.loads(row["approval_token"]) if row["approval_token"] else None,
        "body": row["body"],
        "interaction_mode": row["interaction_mode"],
        "input_artifacts_from": json.loads(row["input_artifacts_from"]),
        "verify": json.loads(row["verify"]),
        "gates": json.loads(row["gates"]),
        "skills": json.loads(row["skills"]),
        "access_levels": json.loads(row["access_levels"]),
        "escalation": json.loads(row["escalation"]),
        "schedule": row["schedule"],
        "on_failure": row["on_failure"],
        "on_stuck": row["on_stuck"],
        "failure_context": row["failure_context"],
        "tasks": json.loads(row["tasks"]),
        "depends_on": json.loads(row["depends_on"]),
        "status": row["status"],
        "attempts": row["attempts"],
        "budget_used": json.loads(row["budget_used"]),
        "verification_results": json.loads(row["verification_results"]),
        "created_at": row["created_at"],
    }
    return WorkItem.model_validate(data)


__all__ = ["SQLiteWorkItemStore"]
