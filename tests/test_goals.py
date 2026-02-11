from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from silas.goals.manager import SilasGoalManager
from silas.memory.consolidator import SilasMemoryConsolidator
from silas.models.context import ContextSubscription, ContextZone
from silas.models.goals import Goal, GoalRun, GoalSchedule, StandingApproval
from silas.models.memory import MemoryItem, MemoryType, ReingestionTier
from silas.models.messages import TaintLevel
from silas.proactivity.calibrator import SimpleAutonomyCalibrator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _goal(*, goal_id: str = "g1", standing_approval: bool = False) -> Goal:
    now = _utc_now()
    return Goal(
        goal_id=goal_id,
        name=f"Goal {goal_id}",
        description="Do the thing",
        schedule=GoalSchedule(kind="interval", interval_seconds=300),
        work_template={
            "type": "task",
            "title": "Execute goal",
            "body": "Run objective",
        },
        skills=["coding"],
        standing_approval=standing_approval,
        created_at=now,
        updated_at=now,
    )


class AsyncCollectingWorkItemStore:
    def __init__(self) -> None:
        self.saved: list[object] = []

    async def save(self, item: object) -> None:
        self.saved.append(item)


class InMemoryMemoryStoreForConsolidator:
    def __init__(self, items: list[MemoryItem]) -> None:
        self.items: dict[str, MemoryItem] = {item.memory_id: item for item in items}

    async def list_recent(self, limit: int) -> list[MemoryItem]:
        values = list(self.items.values())
        values.sort(key=lambda item: (item.updated_at, item.created_at, item.memory_id), reverse=True)
        return values[:limit]

    async def update(self, memory_id: str, **kwargs: object) -> None:
        current = self.items.get(memory_id)
        if current is None:
            return
        payload = current.model_dump(mode="python")
        payload.update(kwargs)
        self.items[memory_id] = MemoryItem.model_validate(payload)

    async def delete(self, memory_id: str) -> None:
        self.items.pop(memory_id, None)


class TestGoalModels:
    def test_goal_model_creation_and_serialization(self) -> None:
        goal = _goal(goal_id="g-model")
        payload = goal.model_dump(mode="json")
        assert payload["goal_id"] == "g-model"
        assert payload["schedule"]["kind"] == "interval"
        assert payload["work_template"]["title"] == "Execute goal"

    def test_goal_schedule_cron_variant(self) -> None:
        schedule = GoalSchedule(kind="cron", cron_expr="*/5 * * * *")
        assert schedule.kind == "cron"
        assert schedule.cron_expr == "*/5 * * * *"

    def test_goal_schedule_interval_variant(self) -> None:
        schedule = GoalSchedule(kind="interval", interval_seconds=60)
        assert schedule.kind == "interval"
        assert schedule.interval_seconds == 60

    def test_goal_schedule_once_variant(self) -> None:
        run_at = _utc_now() + timedelta(hours=1)
        schedule = GoalSchedule(kind="once", run_at=run_at)
        assert schedule.kind == "once"
        assert schedule.run_at == run_at

    def test_goal_run_status_transitions(self) -> None:
        run = GoalRun(run_id="r1", goal_id="g1")
        assert run.status == "pending"
        run.transition_to("running")
        run.transition_to("completed")
        assert run.status == "completed"
        assert run.started_at is not None
        assert run.completed_at is not None

    def test_goal_run_invalid_transition_raises(self) -> None:
        run = GoalRun(run_id="r2", goal_id="g1")
        run.transition_to("running")
        run.transition_to("completed")
        with pytest.raises(ValueError, match="invalid GoalRun transition"):
            run.transition_to("running")

    def test_standing_approval_creation_and_defaults(self) -> None:
        now = _utc_now()
        approval = StandingApproval(
            approval_id="a1",
            goal_id="g1",
            policy_hash="abc",
            granted_by="owner",
            granted_at=now,
            max_uses=3,
        )
        assert approval.uses_remaining == 3

    def test_standing_approval_expiry_validation(self) -> None:
        now = _utc_now()
        with pytest.raises(ValidationError, match="expires_at"):
            StandingApproval(
                approval_id="a2",
                goal_id="g1",
                policy_hash="abc",
                granted_by="owner",
                granted_at=now,
                expires_at=now - timedelta(seconds=1),
            )


class TestGoalManager:
    def test_load_schedule_and_unschedule(self) -> None:
        store = AsyncCollectingWorkItemStore()
        manager = SilasGoalManager(goals_config=[], work_item_store=store)

        goal = _goal(goal_id="g-load")
        manager.schedule_goal(goal)
        loaded = manager.load_goals()

        assert len(loaded) == 1
        assert loaded[0].goal_id == "g-load"
        assert loaded[0].spawn_policy_hash is not None

        manager.unschedule_goal("g-load")
        assert manager.load_goals() == []

    def test_run_goal_creates_goal_run_and_work_item(self) -> None:
        store = AsyncCollectingWorkItemStore()
        manager = SilasGoalManager(goals_config=[_goal(goal_id="g-run")], work_item_store=store)

        run = manager.run_goal("g-run")

        assert run.goal_id == "g-run"
        assert run.status == "completed"
        assert run.work_item_id is not None
        assert len(store.saved) == 1

    def test_standing_approval_grant_revoke_lookup(self) -> None:
        store = AsyncCollectingWorkItemStore()
        manager = SilasGoalManager(goals_config=[_goal(goal_id="g-approve")], work_item_store=store)
        goal = manager.load_goals()[0]
        assert goal.spawn_policy_hash is not None

        granted = manager.grant_standing_approval(
            goal_id=goal.goal_id,
            policy_hash=goal.spawn_policy_hash,
            granted_by="owner",
            expires_at=None,
            max_uses=2,
        )

        loaded = manager.get_standing_approval(goal.goal_id, goal.spawn_policy_hash)
        assert loaded is not None
        assert loaded.approval_id == granted.approval_id

        assert manager.revoke_standing_approval(granted.approval_id) is True
        assert manager.get_standing_approval(goal.goal_id, goal.spawn_policy_hash) is None

    def test_policy_hash_canonicalization(self) -> None:
        store = AsyncCollectingWorkItemStore()
        manager = SilasGoalManager(goals_config=[], work_item_store=store)

        template_a = {"body": "run", "title": "t", "type": "task", "meta": {"b": 2, "a": 1}}
        template_b = {"meta": {"a": 1, "b": 2}, "type": "task", "title": "t", "body": "run"}

        hash_a = manager._compute_policy_hash(template_a)
        hash_b = manager._compute_policy_hash(template_b)

        assert hash_a == hash_b

    def test_standing_approval_max_uses_countdown(self) -> None:
        store = AsyncCollectingWorkItemStore()
        manager = SilasGoalManager(goals_config=[_goal(goal_id="g-uses")], work_item_store=store)
        goal = manager.load_goals()[0]
        assert goal.spawn_policy_hash is not None

        manager.grant_standing_approval(
            goal_id="g-uses",
            policy_hash=goal.spawn_policy_hash,
            granted_by="owner",
            expires_at=None,
            max_uses=2,
        )

        manager.run_goal("g-uses")
        manager.run_goal("g-uses")

        approval = manager.get_standing_approval("g-uses", goal.spawn_policy_hash)
        assert approval is None

        manager.run_goal("g-uses")
        assert len(store.saved) == 3
        assert store.saved[0].needs_approval is False
        assert store.saved[1].needs_approval is False
        assert store.saved[2].needs_approval is True

    def test_standing_approval_expiry_check(self) -> None:
        store = AsyncCollectingWorkItemStore()
        manager = SilasGoalManager(goals_config=[_goal(goal_id="g-exp")], work_item_store=store)
        goal = manager.load_goals()[0]
        assert goal.spawn_policy_hash is not None

        approval = manager.grant_standing_approval(
            goal_id="g-exp",
            policy_hash=goal.spawn_policy_hash,
            granted_by="owner",
            expires_at=_utc_now() + timedelta(hours=1),
            max_uses=None,
        )

        internal = manager._standing_approvals_by_id[approval.approval_id]
        manager._standing_approvals_by_id[approval.approval_id] = internal.model_copy(
            update={"expires_at": _utc_now() - timedelta(seconds=1)}
        )

        loaded = manager.get_standing_approval("g-exp", goal.spawn_policy_hash)
        assert loaded is None

    def test_goal_with_standing_approval_flag_auto_approves(self) -> None:
        store = AsyncCollectingWorkItemStore()
        manager = SilasGoalManager(
            goals_config=[_goal(goal_id="g-auto", standing_approval=True)],
            work_item_store=store,
        )

        run = manager.run_goal("g-auto")

        assert run.status == "completed"
        assert len(store.saved) == 1
        assert store.saved[0].needs_approval is False

    def test_empty_goals_list(self) -> None:
        manager = SilasGoalManager(goals_config=[], work_item_store=AsyncCollectingWorkItemStore())
        assert manager.load_goals() == []

    def test_run_goal_with_missing_goal_id_raises(self) -> None:
        manager = SilasGoalManager(goals_config=[], work_item_store=AsyncCollectingWorkItemStore())
        with pytest.raises(KeyError, match="unknown goal_id"):
            manager.run_goal("missing")


class TestMemoryConsolidator:
    def _memory(self, memory_id: str, **kwargs: object) -> MemoryItem:
        defaults = {
            "memory_id": memory_id,
            "content": "same",
            "memory_type": MemoryType.fact,
            "reingestion_tier": ReingestionTier.active,
            "taint": TaintLevel.owner,
            "source_kind": "test",
            "created_at": _utc_now() - timedelta(days=10),
            "updated_at": _utc_now() - timedelta(days=10),
            "access_count": 0,
            "last_accessed": _utc_now() - timedelta(days=10),
            "session_id": "owner",
        }
        defaults.update(kwargs)
        return MemoryItem(**defaults)

    def test_merge_duplicates(self) -> None:
        old = self._memory("m-old", access_count=1)
        newer = self._memory("m-new", access_count=5)
        store = InMemoryMemoryStoreForConsolidator([old, newer])
        consolidator = SilasMemoryConsolidator(store)

        stats = consolidator.consolidate("owner")

        assert stats["merged"] == 1
        assert len(store.items) == 1
        remaining = next(iter(store.items.values()))
        assert remaining.access_count == 6

    def test_archive_stale_memory(self) -> None:
        stale = self._memory(
            "m-stale",
            content="stale",
            access_count=1,
            last_accessed=_utc_now() - timedelta(days=45),
            updated_at=_utc_now() - timedelta(days=45),
        )
        store = InMemoryMemoryStoreForConsolidator([stale])
        consolidator = SilasMemoryConsolidator(store)

        stats = consolidator.consolidate("owner")

        assert stats["archived"] == 1
        assert store.items["m-stale"].reingestion_tier == ReingestionTier.dormant

    def test_promote_frequent_memory(self) -> None:
        frequent = self._memory("m-frequent", content="freq", access_count=11, last_accessed=_utc_now())
        store = InMemoryMemoryStoreForConsolidator([frequent])
        consolidator = SilasMemoryConsolidator(store)

        stats = consolidator.consolidate("owner")

        assert stats["promoted"] == 1
        assert store.items["m-frequent"].reingestion_tier == ReingestionTier.core


class TestAutonomyCalibrator:
    @pytest.mark.asyncio
    async def test_rollback_reverts_last_threshold_change(self) -> None:
        calibrator = SimpleAutonomyCalibrator(
            window_size=8,
            min_sample_size=5,
            widen_threshold=0.2,
            tighten_threshold=0.8,
            threshold_step=0.1,
        )
        for _ in range(5):
            await calibrator.record_outcome("owner", "direct", "approved")

        await calibrator.evaluate("owner", _utc_now())
        metrics_before = calibrator.get_metrics("owner")
        before_threshold = metrics_before["families"]["direct"]["threshold"]
        assert before_threshold == 0.6

        calibrator.rollback("owner", "direct")
        metrics_after = calibrator.get_metrics("owner")
        after_threshold = metrics_after["families"]["direct"]["threshold"]

        assert after_threshold == 0.5
        assert metrics_after["families"]["direct"]["change_count"] == 0

    @pytest.mark.asyncio
    async def test_get_metrics_returns_rolling_window_stats(self) -> None:
        calibrator = SimpleAutonomyCalibrator(window_size=5, min_sample_size=2)
        await calibrator.record_outcome("owner", "direct", "approved")
        await calibrator.record_outcome("owner", "direct", "declined")
        await calibrator.record_outcome("owner", "planner", "undo")

        metrics = calibrator.get_metrics("owner")

        assert metrics["scope_id"] == "owner"
        assert metrics["total_events"] == 3
        assert metrics["families"]["direct"]["sample_size"] == 2
        assert metrics["families"]["direct"]["corrections"] == 1
        assert metrics["families"]["planner"]["corrections"] == 1


class TestGoalsMigration:
    @pytest.mark.asyncio
    async def test_goals_migration_creates_tables_and_indexes(self, tmp_path: Path) -> None:
        import aiosqlite
        from silas.persistence.migrations import run_migrations

        db_path = tmp_path / "goals.db"
        await run_migrations(str(db_path))

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                ORDER BY name
                """
            )
            table_names = {row[0] for row in await cursor.fetchall()}

            idx_cursor = await db.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                ORDER BY name
                """
            )
            index_names = {row[0] for row in await idx_cursor.fetchall()}

        assert "goals" in table_names
        assert "goal_runs" in table_names
        assert "standing_approvals" in table_names
        assert "idx_goal_runs_goal_started" in index_names
        assert "idx_standing_approvals_goal_policy" in index_names


class TestContextSubscriptionModel:
    def test_context_subscription_creation(self) -> None:
        sub = ContextSubscription(
            sub_id="sub-1",
            sub_type="file",
            target="/tmp/demo.py",
            zone=ContextZone.workspace,
            turn_created=3,
            content_hash="abc123",
        )
        assert sub.active is True
        assert sub.token_count == 0
        assert sub.zone == ContextZone.workspace

    def test_context_subscription_requires_timezone_aware_created_at(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            ContextSubscription(
                sub_id="sub-2",
                sub_type="file",
                target="/tmp/demo.py",
                zone=ContextZone.workspace,
                created_at=datetime(2026, 1, 1),
                turn_created=1,
                content_hash="hash",
            )
