"""Tests for UndoManager — register, execute, expire, concurrency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from silas.core.undo import UndoManager
from silas.models.undo import UndoResult

SCOPE = "test-scope"
EXEC_ID = "exec-001"
REVERSE = [{"action": "delete_file", "path": "/tmp/foo.txt"}]
NOW = datetime(2026, 2, 13, 10, 0, 0, tzinfo=UTC)


def _make_manager(window_s: float = 300) -> UndoManager:
    return UndoManager(undo_window=timedelta(seconds=window_s))


class TestRegisterAndExecute:
    def test_register_and_undo_within_window(self) -> None:
        mgr = _make_manager()
        entry = mgr.record_execution(SCOPE, EXEC_ID, REVERSE, summary="wrote file", now=NOW)

        applied: list[dict[str, object]] = []
        result = mgr.execute_undo(SCOPE, entry.entry_id, apply_reverse_action=applied.append, now=NOW + timedelta(seconds=60))

        assert result.success
        assert not result.expired
        assert len(applied) == 1
        assert applied[0]["action"] == "delete_file"

    def test_is_undoable_true_within_window(self) -> None:
        mgr = _make_manager()
        entry = mgr.record_execution(SCOPE, EXEC_ID, REVERSE, now=NOW)
        assert mgr.is_undoable(SCOPE, entry.entry_id, now=NOW + timedelta(seconds=100))

    def test_is_undoable_false_after_window(self) -> None:
        mgr = _make_manager(window_s=60)
        entry = mgr.record_execution(SCOPE, EXEC_ID, REVERSE, now=NOW)
        assert not mgr.is_undoable(SCOPE, entry.entry_id, now=NOW + timedelta(seconds=61))


class TestExpired:
    def test_undo_after_ttl_returns_expired(self) -> None:
        mgr = _make_manager(window_s=10)
        entry = mgr.record_execution(SCOPE, EXEC_ID, REVERSE, now=NOW)

        result = mgr.execute_undo(SCOPE, entry.entry_id, now=NOW + timedelta(seconds=11))

        assert not result.success
        assert result.expired

    def test_undo_at_exact_expiry_succeeds(self) -> None:
        """Boundary: undo at exactly expires_at should still work (<=)."""
        mgr = _make_manager(window_s=10)
        entry = mgr.record_execution(SCOPE, EXEC_ID, REVERSE, now=NOW)

        result = mgr.execute_undo(SCOPE, entry.entry_id, now=NOW + timedelta(seconds=10))
        assert result.success


class TestNotFound:
    def test_nonexistent_action_id(self) -> None:
        mgr = _make_manager()
        result = mgr.execute_undo(SCOPE, "does-not-exist", now=NOW)
        assert not result.success
        assert "No undo entry" in result.message

    def test_is_undoable_nonexistent(self) -> None:
        mgr = _make_manager()
        assert not mgr.is_undoable(SCOPE, "nope", now=NOW)


class TestPruneExpired:
    def test_expire_old_cleans_up(self) -> None:
        mgr = _make_manager(window_s=10)
        mgr.record_execution(SCOPE, "a", REVERSE, now=NOW)
        mgr.record_execution(SCOPE, "b", REVERSE, now=NOW + timedelta(seconds=5))

        # At NOW+11, first entry expired; second still alive
        removed = mgr.prune_expired(now=NOW + timedelta(seconds=11))
        assert removed == 1
        assert len(mgr.list_active(SCOPE, now=NOW + timedelta(seconds=11))) == 1

    def test_prune_all_removes_scope(self) -> None:
        mgr = _make_manager(window_s=1)
        mgr.record_execution(SCOPE, "x", REVERSE, now=NOW)
        mgr.prune_expired(now=NOW + timedelta(seconds=5))
        # Scope dict should be cleaned up
        assert SCOPE not in mgr._entries_by_scope


class TestConcurrentUndo:
    def test_only_first_undo_succeeds(self) -> None:
        """Simulate two callers trying to undo the same action — only first wins."""
        mgr = _make_manager()
        entry = mgr.record_execution(SCOPE, EXEC_ID, REVERSE, now=NOW)
        undo_time = NOW + timedelta(seconds=30)

        first = mgr.execute_undo(SCOPE, entry.entry_id, now=undo_time)
        second = mgr.execute_undo(SCOPE, entry.entry_id, now=undo_time)

        assert first.success
        assert not second.success
        assert "already undone" in second.message.lower()


class TestUndoResult:
    def test_model_fields(self) -> None:
        r = UndoResult(success=True, message="ok", entry_id="e1")
        assert r.expired is False
        assert r.entry_id == "e1"
