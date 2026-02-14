"""Tests for SilasScheduler — APScheduler wrapper."""

from __future__ import annotations

import pytest
from silas.scheduler.ap_scheduler import SilasScheduler
from tests.helpers import wait_until


@pytest.fixture
def scheduler() -> SilasScheduler:
    return SilasScheduler()


class TestSchedulerLifecycle:
    def test_initial_state(self, scheduler: SilasScheduler) -> None:
        assert not scheduler.running
        assert scheduler.job_ids == []

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        s = SilasScheduler()
        await s.start()
        assert s.running
        await s.shutdown()
        assert not s.running

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        s = SilasScheduler()
        await s.start()
        await s.start()  # Should not raise
        assert s.running
        await s.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent(self, scheduler: SilasScheduler) -> None:
        await scheduler.shutdown()  # Not running, should not raise
        assert not scheduler.running


class TestGoalSchedule:
    def test_add_goal_schedule(self, scheduler: SilasScheduler) -> None:
        async def noop() -> None:
            pass

        sid = scheduler.add_goal_schedule("g1", "*/5 * * * *", noop)
        assert sid == "goal:g1"
        assert "goal:g1" in scheduler.job_ids

    def test_duplicate_goal_raises(self, scheduler: SilasScheduler) -> None:
        async def noop() -> None:
            pass

        scheduler.add_goal_schedule("g1", "*/5 * * * *", noop)
        with pytest.raises(ValueError, match="already registered"):
            scheduler.add_goal_schedule("g1", "*/10 * * * *", noop)

    def test_invalid_cron_raises(self, scheduler: SilasScheduler) -> None:
        async def noop() -> None:
            pass

        with pytest.raises(ValueError):
            scheduler.add_goal_schedule("g1", "not a cron", noop)


class TestHeartbeat:
    def test_add_heartbeat(self, scheduler: SilasScheduler) -> None:
        async def noop() -> None:
            pass

        sid = scheduler.add_heartbeat("suggestion_engine", 60, noop)
        assert sid == "heartbeat:suggestion_engine"
        assert "heartbeat:suggestion_engine" in scheduler.job_ids

    def test_duplicate_heartbeat_raises(self, scheduler: SilasScheduler) -> None:
        async def noop() -> None:
            pass

        scheduler.add_heartbeat("hb1", 30, noop)
        with pytest.raises(ValueError, match="already registered"):
            scheduler.add_heartbeat("hb1", 60, noop)

    def test_zero_interval_raises(self, scheduler: SilasScheduler) -> None:
        async def noop() -> None:
            pass

        with pytest.raises(ValueError, match="must be > 0"):
            scheduler.add_heartbeat("hb1", 0, noop)

    def test_negative_interval_raises(self, scheduler: SilasScheduler) -> None:
        async def noop() -> None:
            pass

        with pytest.raises(ValueError, match="must be > 0"):
            scheduler.add_heartbeat("hb1", -10, noop)


class TestRemoveSchedule:
    def test_remove_existing(self, scheduler: SilasScheduler) -> None:
        async def noop() -> None:
            pass

        scheduler.add_heartbeat("hb1", 30, noop)
        scheduler.remove_schedule("heartbeat:hb1")
        assert "heartbeat:hb1" not in scheduler.job_ids

    def test_remove_unknown_raises(self, scheduler: SilasScheduler) -> None:
        with pytest.raises(KeyError, match="unknown schedule"):
            scheduler.remove_schedule("nonexistent")


class TestCallbackExecution:
    @pytest.mark.asyncio
    async def test_heartbeat_fires(self) -> None:
        """Verify a heartbeat callback actually fires when the scheduler runs."""
        scheduler = SilasScheduler()
        call_count = 0

        async def counter() -> None:
            nonlocal call_count
            call_count += 1

        scheduler.add_heartbeat("test", 0.1, counter)
        await scheduler.start()

        await wait_until(lambda: call_count >= 1, timeout=0.5)

        await scheduler.shutdown()
        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash(self) -> None:
        """A failing callback should be caught — scheduler stays alive."""
        scheduler = SilasScheduler()
        healthy_count = 0

        async def failing() -> None:
            raise RuntimeError("boom")

        async def healthy() -> None:
            nonlocal healthy_count
            healthy_count += 1

        scheduler.add_heartbeat("bad", 0.1, failing)
        scheduler.add_heartbeat("good", 0.1, healthy)
        await scheduler.start()

        await wait_until(lambda: healthy_count >= 1, timeout=0.5)

        await scheduler.shutdown()
        # The healthy callback should still have fired despite the failing one
        assert healthy_count >= 1


class TestStopClearsJobs:
    @pytest.mark.asyncio
    async def test_stop_clears_all_jobs(self) -> None:
        s = SilasScheduler()

        async def noop() -> None:
            pass

        s.add_goal_schedule("g1", "*/5 * * * *", noop)
        s.add_heartbeat("hb1", 30, noop)
        assert len(s.job_ids) == 2

        await s.start()
        await s.shutdown()

        # All jobs should be cleared after stop
        assert s.job_ids == []
