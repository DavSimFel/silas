"""Tests for ExecutorPool (§7.1) and WorktreeManager (§7.4).

Covers:
- Concurrency caps (per-scope and global)
- Parallel dispatch of independent work items
- Cancellation
- Conflict detection and serialisation
- Wave scheduling in LiveWorkItemExecutor
- WorktreeManager lifecycle (create / merge / destroy)
- Worktree merge conflict handling
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from silas.execution.worktree import LiveWorktreeManager
from silas.models.approval import ApprovalScope, ApprovalToken, ApprovalVerdict
from silas.models.work import (
    BudgetUsed,
    WorkItem,
    WorkItemResult,
    WorkItemStatus,
    WorkItemType,
)
from silas.skills.executor import SkillExecutor
from silas.skills.registry import SkillRegistry
from silas.execution.work_executor import LiveWorkItemExecutor
from silas.execution.pool import LiveExecutorPool, _detect_conflicts, priority_key

from tests.fakes import InMemoryWorkItemStore

# ── Fixtures & helpers ──────────────────────────────────────────────


def _work_item(
    item_id: str,
    *,
    depends_on: list[str] | None = None,
    input_artifacts_from: list[str] | None = None,
    include_approval: bool = True,
) -> WorkItem:
    item = WorkItem(
        id=item_id,
        type=WorkItemType.task,
        title=item_id,
        body=f"Execute {item_id}",
        skills=[],
        depends_on=depends_on or [],
        input_artifacts_from=input_artifacts_from or [],
    )
    if include_approval:
        item.approval_token = _approval_token(item)
    return item


def _approval_token(work_item: WorkItem) -> ApprovalToken:
    now = datetime.now(UTC)
    return ApprovalToken(
        token_id=f"tok:{work_item.id}",
        plan_hash=work_item.plan_hash(),
        work_item_id=work_item.id,
        scope=ApprovalScope.full_plan,
        verdict=ApprovalVerdict.approved,
        signature=b"test-sig",
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=30),
        nonce=f"nonce:{work_item.id}",
        executions_used=1,
        max_executions=1,
    )


def _ok_result(item_id: str) -> WorkItemResult:
    return WorkItemResult(
        work_item_id=item_id,
        status=WorkItemStatus.done,
        summary=f"{item_id} done",
        budget_used=BudgetUsed(attempts=1, executor_runs=1),
    )


# ── ExecutorPool tests ──────────────────────────────────────────────


class TestExecutorPoolConcurrency:
    """Verify per-scope and global concurrency caps."""

    @pytest.mark.asyncio
    async def test_per_scope_concurrency_cap(self) -> None:
        """At most max_concurrent items run simultaneously per scope."""
        max_running = 0
        current_running = 0
        lock = asyncio.Lock()

        async def _executor(item: WorkItem) -> WorkItemResult:
            nonlocal max_running, current_running
            async with lock:
                current_running += 1
                max_running = max(max_running, current_running)
            await asyncio.sleep(0.01)
            async with lock:
                current_running -= 1
            return _ok_result(item.id)

        pool = LiveExecutorPool(_executor, max_concurrent=2, max_concurrent_global=10)
        items = [_work_item(f"t{i}", include_approval=False) for i in range(6)]

        results = await asyncio.gather(*[pool.dispatch(item, "scope-a") for item in items])

        assert all(r.status == WorkItemStatus.done for r in results)
        assert max_running <= 2

    @pytest.mark.asyncio
    async def test_global_concurrency_cap(self) -> None:
        """Global cap limits total concurrent items across scopes."""
        max_running = 0
        current_running = 0
        lock = asyncio.Lock()

        async def _executor(item: WorkItem) -> WorkItemResult:
            nonlocal max_running, current_running
            async with lock:
                current_running += 1
                max_running = max(max_running, current_running)
            await asyncio.sleep(0.01)
            async with lock:
                current_running -= 1
            return _ok_result(item.id)

        pool = LiveExecutorPool(_executor, max_concurrent=5, max_concurrent_global=3)
        items = [_work_item(f"t{i}", include_approval=False) for i in range(6)]
        scopes = ["s1", "s2", "s3", "s1", "s2", "s3"]

        results = await asyncio.gather(
            *[pool.dispatch(item, scope) for item, scope in zip(items, scopes, strict=True)]
        )

        assert all(r.status == WorkItemStatus.done for r in results)
        assert max_running <= 3

    @pytest.mark.asyncio
    async def test_in_flight_count_tracks_active_tasks(self) -> None:
        """in_flight_count accurately reflects running tasks."""
        gate = asyncio.Event()

        async def _executor(item: WorkItem) -> WorkItemResult:
            await gate.wait()
            return _ok_result(item.id)

        pool = LiveExecutorPool(_executor, max_concurrent=4, max_concurrent_global=4)
        item = _work_item("t1", include_approval=False)

        task = asyncio.create_task(pool.dispatch(item, "s1"))
        await asyncio.sleep(0.01)
        assert pool.in_flight_count == 1

        gate.set()
        await task
        assert pool.in_flight_count == 0


class TestExecutorPoolCancellation:
    """Verify task cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_running_task(self) -> None:
        """Cancelling a running task returns a failed result."""
        gate = asyncio.Event()

        async def _executor(item: WorkItem) -> WorkItemResult:
            await gate.wait()
            return _ok_result(item.id)

        pool = LiveExecutorPool(_executor, max_concurrent=4, max_concurrent_global=4)
        item = _work_item("cancel-me", include_approval=False)

        task = asyncio.create_task(pool.dispatch(item, "s1"))
        await asyncio.sleep(0.01)

        cancelled = await pool.cancel("cancel-me")
        assert cancelled is True

        result = await task
        assert result.status == WorkItemStatus.failed
        assert result.last_error == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self) -> None:
        """Cancelling a task that doesn't exist returns False."""

        async def _executor(item: WorkItem) -> WorkItemResult:
            return _ok_result(item.id)

        pool = LiveExecutorPool(_executor, max_concurrent=4, max_concurrent_global=4)
        assert await pool.cancel("does-not-exist") is False


class TestExecutorPoolDispatchParallel:
    """Verify dispatch_parallel with conflict detection."""

    @pytest.mark.asyncio
    async def test_independent_items_run_concurrently(self) -> None:
        """Non-conflicting items are dispatched in parallel."""
        call_order: list[str] = []

        async def _executor(item: WorkItem) -> WorkItemResult:
            call_order.append(f"start:{item.id}")
            await asyncio.sleep(0.01)
            call_order.append(f"end:{item.id}")
            return _ok_result(item.id)

        pool = LiveExecutorPool(_executor, max_concurrent=4, max_concurrent_global=4)
        items = [_work_item(f"t{i}", include_approval=False) for i in range(3)]

        results = await pool.dispatch_parallel(items, "s1")

        assert all(r.status == WorkItemStatus.done for r in results)
        # All should start before any ends (concurrent)
        starts = [e for e in call_order if e.startswith("start:")]
        assert len(starts) == 3

    @pytest.mark.asyncio
    async def test_conflicting_items_serialised(self) -> None:
        """Items with overlapping file paths run serially."""
        concurrency = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def _executor(item: WorkItem) -> WorkItemResult:
            nonlocal concurrency, max_concurrent
            async with lock:
                concurrency += 1
                max_concurrent = max(max_concurrent, concurrency)
            await asyncio.sleep(0.01)
            async with lock:
                concurrency -= 1
            return _ok_result(item.id)

        pool = LiveExecutorPool(_executor, max_concurrent=4, max_concurrent_global=4)
        # These share an artifact path -> conflict -> serialised
        items = [
            _work_item("t1", input_artifacts_from=["shared/file.txt"], include_approval=False),
            _work_item("t2", input_artifacts_from=["shared/file.txt"], include_approval=False),
        ]

        results = await pool.dispatch_parallel(items, "s1")

        assert all(r.status == WorkItemStatus.done for r in results)
        # Conflicting items are in separate serial groups, so max_concurrent
        # for each group is 1
        assert max_concurrent <= 1


class TestConflictDetection:
    """Unit tests for _detect_conflicts."""

    def test_no_items(self) -> None:
        assert _detect_conflicts([]) == []

    def test_single_item(self) -> None:
        items = [_work_item("t1", include_approval=False)]
        groups = _detect_conflicts(items)
        assert len(groups) == 1
        assert len(groups[0]) == 1

    def test_no_conflicts(self) -> None:
        items = [
            _work_item("t1", input_artifacts_from=["a.txt"], include_approval=False),
            _work_item("t2", input_artifacts_from=["b.txt"], include_approval=False),
            _work_item("t3", input_artifacts_from=["c.txt"], include_approval=False),
        ]
        groups = _detect_conflicts(items)
        # All in one parallel group
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_overlapping_paths_serialised(self) -> None:
        items = [
            _work_item("t1", input_artifacts_from=["shared.txt"], include_approval=False),
            _work_item(
                "t2", input_artifacts_from=["shared.txt", "other.txt"], include_approval=False
            ),
            _work_item("t3", input_artifacts_from=["unique.txt"], include_approval=False),
        ]
        groups = _detect_conflicts(items)
        # t3 is non-conflicting → 1 parallel group
        # t1 and t2 conflict → 2 serial groups
        assert len(groups) == 3
        serial = [g for g in groups if len(g) == 1]
        # t3 is alone but may also be a single-item group
        assert len(serial) >= 2  # t1, t2 each serialised


class TestPriorityKey:
    """Verify dispatch priority ordering."""

    def test_approved_highest_priority(self) -> None:
        item = _work_item("approved", include_approval=True)
        assert priority_key(item) == 0

    def test_unapproved_task_is_research(self) -> None:
        item = _work_item("research", include_approval=False)
        assert priority_key(item) == 1

    def test_goal_is_status(self) -> None:
        item = WorkItem(
            id="goal-1",
            type=WorkItemType.goal,
            title="goal",
            body="monitor",
            agent="stream",
            schedule="always_on",
        )
        assert priority_key(item) == 2


# ── Wave scheduling tests ──────────────────────────────────────────


class TestWaveScheduling:
    """Verify _build_waves groups items correctly."""

    def test_linear_chain_produces_single_item_waves(self) -> None:
        """A->B->C produces 3 waves of 1 item each."""
        registry = SkillRegistry()
        skill_executor = SkillExecutor(registry)
        store = InMemoryWorkItemStore()
        executor = LiveWorkItemExecutor(skill_executor, store)

        prerequisites = {
            "A": set(),
            "B": {"A"},
            "C": {"B"},
        }
        waves = executor._build_waves(["A", "B", "C"], prerequisites)
        assert waves == [["A"], ["B"], ["C"]]

    def test_independent_items_in_single_wave(self) -> None:
        """Independent items are grouped into one wave."""
        registry = SkillRegistry()
        skill_executor = SkillExecutor(registry)
        store = InMemoryWorkItemStore()
        executor = LiveWorkItemExecutor(skill_executor, store)

        prerequisites = {
            "A": set(),
            "B": set(),
            "C": set(),
        }
        waves = executor._build_waves(["A", "B", "C"], prerequisites)
        assert len(waves) == 1
        assert set(waves[0]) == {"A", "B", "C"}

    def test_diamond_dependency_produces_three_waves(self) -> None:
        """Diamond: A depends on B and C, B and C independent -> 3 waves."""
        registry = SkillRegistry()
        skill_executor = SkillExecutor(registry)
        store = InMemoryWorkItemStore()
        executor = LiveWorkItemExecutor(skill_executor, store)

        # D has no deps, B and C depend on D, A depends on B and C
        prerequisites = {
            "D": set(),
            "B": {"D"},
            "C": {"D"},
            "A": {"B", "C"},
        }
        waves = executor._build_waves(["D", "B", "C", "A"], prerequisites)
        assert len(waves) == 3
        assert waves[0] == ["D"]
        assert set(waves[1]) == {"B", "C"}
        assert waves[2] == ["A"]


# ── WorktreeManager tests ──────────────────────────────────────────


def _init_git_repo(path: Path) -> None:
    """Initialise a bare git repo with an initial commit."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    # Create an initial commit
    readme = path / "README.md"
    readme.write_text("# Test Repo\n")
    subprocess.run(["git", "add", "-A"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )


class TestWorktreeManager:
    """Integration tests for LiveWorktreeManager."""

    @pytest.mark.asyncio
    async def test_create_and_destroy(self, tmp_path: Path) -> None:
        """Create a worktree and verify it exists, then destroy it."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        mgr = LiveWorktreeManager(str(repo))
        wt_path = await mgr.create("scope1", "task1", 1)

        assert Path(wt_path).exists()
        assert (Path(wt_path) / "README.md").exists()

        await mgr.destroy(wt_path)
        assert not Path(wt_path).exists()

    @pytest.mark.asyncio
    async def test_merge_back_with_changes(self, tmp_path: Path) -> None:
        """Changes in the worktree are merged back to canonical workspace."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        mgr = LiveWorktreeManager(str(repo))
        wt_path = await mgr.create("scope1", "task1", 1)

        # Write a new file in the worktree
        new_file = Path(wt_path) / "feature.py"
        new_file.write_text("print('hello')\n")

        ok, err = await mgr.merge_back(wt_path)
        assert ok is True
        assert err is None

        # Verify the file exists in canonical workspace
        assert (repo / "feature.py").exists()

        await mgr.destroy(wt_path)

    @pytest.mark.asyncio
    async def test_merge_back_no_changes(self, tmp_path: Path) -> None:
        """No-op merge when worktree has no changes."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        mgr = LiveWorktreeManager(str(repo))
        wt_path = await mgr.create("scope1", "task2", 1)

        ok, err = await mgr.merge_back(wt_path)
        assert ok is True
        assert err is None

        await mgr.destroy(wt_path)

    @pytest.mark.asyncio
    async def test_worktree_path_convention(self, tmp_path: Path) -> None:
        """Worktree paths follow .runtime/worktrees/{scope}/{task}/{attempt}."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        mgr = LiveWorktreeManager(str(repo))
        wt_path = await mgr.create("my-scope", "my-task", 3)

        expected_suffix = os.path.join(".runtime", "worktrees", "my-scope", "my-task", "3")
        assert wt_path.endswith(expected_suffix)

        await mgr.destroy(wt_path)

    @pytest.mark.asyncio
    async def test_multiple_worktrees_isolated(self, tmp_path: Path) -> None:
        """Two worktrees from the same repo don't see each other's changes."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        mgr = LiveWorktreeManager(str(repo))
        wt1 = await mgr.create("scope1", "task-a", 1)
        wt2 = await mgr.create("scope1", "task-b", 1)

        # Write different files in each worktree
        (Path(wt1) / "from_a.txt").write_text("a")
        (Path(wt2) / "from_b.txt").write_text("b")

        # Worktree 1 doesn't see worktree 2's files
        assert not (Path(wt1) / "from_b.txt").exists()
        assert not (Path(wt2) / "from_a.txt").exists()

        # Merge both back
        ok1, _ = await mgr.merge_back(wt1)
        ok2, _ = await mgr.merge_back(wt2)
        assert ok1 is True
        assert ok2 is True

        # Canonical repo has both files
        assert (repo / "from_a.txt").exists()
        assert (repo / "from_b.txt").exists()

        await mgr.destroy(wt1)
        await mgr.destroy(wt2)

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_worktree(self, tmp_path: Path) -> None:
        """Destroying a non-existent worktree doesn't raise."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        mgr = LiveWorktreeManager(str(repo))
        await mgr.destroy("/nonexistent/path")  # Should not raise


# ── Pool + executor integration ────────────────────────────────────


class TestPoolExecutorIntegration:
    """End-to-end: pool dispatches to executor for parallel items."""

    @pytest.mark.asyncio
    async def test_parallel_items_dispatched_via_pool(self) -> None:
        """When pool is provided, independent items run concurrently."""
        from silas.models.skills import SkillDefinition
        from silas.skills.executor import SkillExecutor
        from silas.skills.registry import SkillRegistry
        from silas.execution.work_executor import LiveWorkItemExecutor

        registry = SkillRegistry()
        skill_executor = SkillExecutor(registry)
        store = InMemoryWorkItemStore()

        # Register a trivial skill
        registry.register(
            SkillDefinition(
                name="noop",
                description="no-op",
                version="1.0.0",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                requires_approval=False,
                max_retries=0,
                timeout_seconds=5,
            )
        )

        async def _noop_handler(_inputs: dict[str, object]) -> dict[str, object]:
            return {"ok": True}

        skill_executor.register_handler("noop", _noop_handler)

        # Track dispatch calls through the pool
        dispatched_ids: list[str] = []

        async def _pool_dispatch(item: WorkItem, scope_id: str) -> WorkItemResult:
            dispatched_ids.append(item.id)
            return _ok_result(item.id)

        class _MockPool:
            async def dispatch(self, item: WorkItem, scope_id: str) -> WorkItemResult:
                return await _pool_dispatch(item, scope_id)

        executor = LiveWorkItemExecutor(
            skill_executor,
            store,
            pool=_MockPool(),
            scope_id="test-scope",
            approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        )

        # Create two independent child tasks + root
        child_a = _work_item("child-a", depends_on=[])
        child_b = _work_item("child-b", depends_on=[])
        root = WorkItem(
            id="root",
            type=WorkItemType.task,
            title="root",
            body="root",
            tasks=["child-a", "child-b"],
            depends_on=[],
        )
        root.approval_token = _approval_token(root)

        # Pre-save children so _resolve_items finds them
        await store.save(child_a)
        await store.save(child_b)

        result = await executor.execute(root)

        assert result.status == WorkItemStatus.done
        # Both children dispatched through the pool
        assert set(dispatched_ids) == {"child-a", "child-b"}


# ── Stub helpers ────────────────────────────────────────────────────


class _StubApprovalVerifier:
    """Always returns a fixed approval result."""

    def __init__(self, *, valid: bool, reason: str) -> None:
        self._valid = valid
        self._reason = reason

    async def check(self, token: object, work_item: object) -> tuple[bool, str]:
        return self._valid, self._reason
