from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from silas.models.undo import UndoEntry, UndoResult


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class UndoManager:
    def __init__(self, undo_window: timedelta = timedelta(minutes=5)) -> None:
        if undo_window.total_seconds() <= 0:
            raise ValueError("undo_window must be positive")
        self._undo_window = undo_window
        self._entries_by_scope: dict[str, dict[str, UndoEntry]] = {}

    @property
    def undo_window(self) -> timedelta:
        return self._undo_window

    def record_execution(
        self,
        scope_id: str,
        execution_id: str,
        reverse_actions: list[dict[str, object]],
        *,
        summary: str = "",
        metadata: dict[str, object] | None = None,
        entry_id: str | None = None,
        now: datetime | None = None,
    ) -> UndoEntry:
        now_utc = now if now is not None else _utc_now()
        _require_timezone_aware(now_utc, "now")

        undo_entry = UndoEntry(
            entry_id=entry_id or f"undo:{scope_id}:{execution_id}:{uuid.uuid4().hex}",
            scope_id=scope_id,
            execution_id=execution_id,
            reverse_actions=[dict(action) for action in reverse_actions],
            summary=summary,
            metadata={} if metadata is None else dict(metadata),
            created_at=now_utc,
            expires_at=now_utc + self._undo_window,
        )
        self._entries_by_scope.setdefault(scope_id, {})[undo_entry.entry_id] = undo_entry
        return undo_entry.model_copy(deep=True)

    def get_entry(self, scope_id: str, entry_id: str) -> UndoEntry | None:
        entry = self._entries_by_scope.get(scope_id, {}).get(entry_id)
        if entry is None:
            return None
        return entry.model_copy(deep=True)

    def list_active(self, scope_id: str, now: datetime | None = None) -> list[UndoEntry]:
        now_utc = now if now is not None else _utc_now()
        _require_timezone_aware(now_utc, "now")

        active = [
            entry.model_copy(deep=True)
            for entry in self._entries_by_scope.get(scope_id, {}).values()
            if entry.can_undo(now_utc)
        ]
        active.sort(key=lambda entry: entry.created_at, reverse=True)
        return active

    def undo(
        self,
        scope_id: str,
        entry_id: str,
        *,
        apply_reverse_action: Callable[[dict[str, object]], None] | None = None,
        now: datetime | None = None,
    ) -> bool:
        now_utc = now if now is not None else _utc_now()
        _require_timezone_aware(now_utc, "now")

        entry = self._entries_by_scope.get(scope_id, {}).get(entry_id)
        if entry is None:
            return False
        if not entry.can_undo(now_utc):
            return False

        for action in reversed(entry.reverse_actions):
            if apply_reverse_action is not None:
                apply_reverse_action(dict(action))

        self._entries_by_scope[scope_id][entry_id] = entry.model_copy(update={"undone_at": now_utc})
        return True

    def is_undoable(self, scope_id: str, entry_id: str, *, now: datetime | None = None) -> bool:
        """Quick check whether an action can still be reversed."""
        now_utc = now if now is not None else _utc_now()
        _require_timezone_aware(now_utc, "now")
        entry = self._entries_by_scope.get(scope_id, {}).get(entry_id)
        if entry is None:
            return False
        return entry.can_undo(now_utc)

    def execute_undo(
        self,
        scope_id: str,
        entry_id: str,
        *,
        apply_reverse_action: Callable[[dict[str, object]], None] | None = None,
        now: datetime | None = None,
    ) -> UndoResult:
        """Typed undo — returns structured result instead of bare bool.

        Why: callers (approval surface, API) need to distinguish between
        expired, not-found, already-undone, and success without inspecting
        the entry themselves.
        """
        now_utc = now if now is not None else _utc_now()
        _require_timezone_aware(now_utc, "now")

        entry = self._entries_by_scope.get(scope_id, {}).get(entry_id)
        if entry is None:
            return UndoResult(
                success=False,
                message=f"No undo entry found for {entry_id}",
                entry_id=entry_id,
            )
        if entry.undone_at is not None:
            return UndoResult(
                success=False,
                message="Action was already undone",
                entry_id=entry_id,
            )
        if now_utc > entry.expires_at:
            return UndoResult(
                success=False,
                message="Undo window has expired",
                expired=True,
                entry_id=entry_id,
            )

        # Apply reverse actions in LIFO order — last effect reversed first
        for action in reversed(entry.reverse_actions):
            if apply_reverse_action is not None:
                apply_reverse_action(dict(action))

        self._entries_by_scope[scope_id][entry_id] = entry.model_copy(update={"undone_at": now_utc})
        return UndoResult(
            success=True,
            message=f"Undone: {entry.summary}" if entry.summary else "Action undone",
            entry_id=entry_id,
        )

    def build_post_execution_card(
        self,
        scope_id: str,
        entry_id: str,
        *,
        results: list[dict[str, object]] | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        now_utc = now if now is not None else _utc_now()
        _require_timezone_aware(now_utc, "now")

        entry = self._entries_by_scope.get(scope_id, {}).get(entry_id)
        if entry is None:
            return {
                "type": "post_execution",
                "scope_id": scope_id,
                "undo_entry_id": entry_id,
                "undo_available": False,
                "undo_label": "Undo unavailable",
                "results": [] if results is None else list(results),
                "generated_at": now_utc,
            }

        can_undo = entry.can_undo(now_utc)
        status = "available"
        if entry.undone_at is not None:
            status = "already_undone"
        elif now_utc > entry.expires_at:
            status = "expired"

        return {
            "type": "post_execution",
            "scope_id": scope_id,
            "undo_entry_id": entry.entry_id,
            "execution_id": entry.execution_id,
            "summary": entry.summary,
            "metadata": dict(entry.metadata),
            "results": [] if results is None else list(results),
            "undo_available": can_undo,
            "undo_label": "Undo" if can_undo else "Undo unavailable",
            "undo_status": status,
            "undo_expires_at": entry.expires_at,
            "generated_at": now_utc,
        }

    def prune_expired(self, scope_id: str | None = None, now: datetime | None = None) -> int:
        now_utc = now if now is not None else _utc_now()
        _require_timezone_aware(now_utc, "now")

        removed = 0
        scope_ids = [scope_id] if scope_id is not None else list(self._entries_by_scope)
        for current_scope in scope_ids:
            entries = self._entries_by_scope.get(current_scope, {})
            expired_ids = [entry_id for entry_id, entry in entries.items() if now_utc > entry.expires_at]
            for entry_id in expired_ids:
                del entries[entry_id]
            removed += len(expired_ids)
            if not entries and current_scope in self._entries_by_scope:
                del self._entries_by_scope[current_scope]
        return removed


__all__ = ["UndoManager"]
