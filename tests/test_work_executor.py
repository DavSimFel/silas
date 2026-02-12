from __future__ import annotations

import pytest
from silas.models.skills import SkillDefinition
from silas.models.work import Budget, WorkItem, WorkItemStatus, WorkItemType
from silas.skills.executor import SkillExecutor
from silas.skills.registry import SkillRegistry
from silas.work.executor import LiveWorkItemExecutor

from tests.fakes import InMemoryWorkItemStore


def _register_skill(
    registry: SkillRegistry,
    name: str,
    *,
    requires_approval: bool = False,
) -> None:
    registry.register(
        SkillDefinition(
            name=name,
            description=f"{name} test skill",
            version="1.0.0",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            requires_approval=requires_approval,
            max_retries=0,
            timeout_seconds=5,
        )
    )


def _work_item(
    item_id: str,
    *,
    title: str | None = None,
    skills: list[str] | None = None,
    depends_on: list[str] | None = None,
    status: WorkItemStatus = WorkItemStatus.pending,
    budget: Budget | None = None,
    needs_approval: bool = False,
) -> WorkItem:
    return WorkItem(
        id=item_id,
        type=WorkItemType.task,
        title=title or item_id,
        body=f"Execute {item_id}",
        skills=skills or [],
        depends_on=depends_on or [],
        status=status,
        budget=budget or Budget(),
        needs_approval=needs_approval,
    )


@pytest.fixture
def work_store() -> InMemoryWorkItemStore:
    return InMemoryWorkItemStore()


@pytest.fixture
def skill_registry() -> SkillRegistry:
    return SkillRegistry()


@pytest.fixture
def skill_executor(skill_registry: SkillRegistry) -> SkillExecutor:
    return SkillExecutor(skill_registry=skill_registry)


@pytest.fixture
def work_executor(
    work_store: InMemoryWorkItemStore,
    skill_executor: SkillExecutor,
) -> LiveWorkItemExecutor:
    return LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
    )


class _FailOnDoneSaveStore(InMemoryWorkItemStore):
    async def save(self, item: WorkItem) -> None:
        if item.status == WorkItemStatus.done:
            raise RuntimeError("simulated done-state persistence failure")
        await super().save(item)


@pytest.mark.asyncio
async def test_single_task_execution(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "skill_a")
    calls: list[str] = []

    async def _handler(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _handler)

    result = await work_executor.execute(_work_item("task-a", skills=["skill_a"]))

    assert result.status == WorkItemStatus.done
    assert calls == ["task-a"]
    loaded = await work_store.get("task-a")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.done


@pytest.mark.asyncio
async def test_dependency_ordering_runs_dependencies_first(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "skill_a")
    _register_skill(skill_registry, "skill_b")
    calls: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    async def _skill_b(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    skill_executor.register_handler("skill_b", _skill_b)

    dep = _work_item("task-a", skills=["skill_a"])
    root = _work_item("task-b", skills=["skill_b"], depends_on=["task-a"])
    await work_store.save(dep)

    result = await work_executor.execute(root)

    assert result.status == WorkItemStatus.done
    assert calls == ["task-a", "task-b"]
    loaded_dep = await work_store.get("task-a")
    loaded_root = await work_store.get("task-b")
    assert loaded_dep is not None
    assert loaded_dep.status == WorkItemStatus.done
    assert loaded_root is not None
    assert loaded_root.status == WorkItemStatus.done


@pytest.mark.asyncio
async def test_circular_dependency_detection_sets_failed_status(
    work_executor: LiveWorkItemExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    item_a = _work_item("task-a", depends_on=["task-b"])
    item_b = _work_item("task-b", depends_on=["task-a"])
    await work_store.save(item_b)

    result = await work_executor.execute(item_a)

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "circular dependency" in result.last_error
    loaded = await work_store.get("task-a")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed


@pytest.mark.asyncio
async def test_retry_on_failure_then_success(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "flaky")
    attempts = {"count": 0}

    async def _flaky(_inputs: dict[str, object]) -> dict[str, object]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("boom")
        return {"ok": True}

    skill_executor.register_handler("flaky", _flaky)

    item = _work_item(
        "task-retry",
        skills=["flaky"],
        budget=Budget(max_attempts=3),
    )
    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.done
    assert attempts["count"] == 2
    loaded = await work_store.get("task-retry")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.done
    assert loaded.attempts == 2


@pytest.mark.asyncio
async def test_retry_exhaustion_marks_failed(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "always_fail")

    async def _always_fail(_inputs: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("still failing")

    skill_executor.register_handler("always_fail", _always_fail)

    item = _work_item(
        "task-fail",
        skills=["always_fail"],
        budget=Budget(max_attempts=2),
    )
    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.failed
    assert result.last_error == "still failing"
    loaded = await work_store.get("task-fail")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed
    assert loaded.attempts == 2


@pytest.mark.asyncio
async def test_budget_tracking_populates_budget_used(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "token_skill")

    async def _token_skill(_inputs: dict[str, object]) -> dict[str, object]:
        return {"payload": "x" * 120}

    skill_executor.register_handler("token_skill", _token_skill)

    result = await work_executor.execute(_work_item("task-budget", skills=["token_skill"]))

    assert result.status == WorkItemStatus.done
    assert result.budget_used.tokens > 0
    assert result.budget_used.attempts == 1
    assert result.budget_used.executor_runs == 1
    loaded = await work_store.get("task-budget")
    assert loaded is not None
    assert loaded.budget_used.tokens > 0


@pytest.mark.asyncio
async def test_skill_not_found_marks_failed(
    work_executor: LiveWorkItemExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    result = await work_executor.execute(_work_item("task-missing-skill", skills=["missing_skill"]))

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "not registered" in result.last_error
    loaded = await work_store.get("task-missing-skill")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed


@pytest.mark.asyncio
async def test_missing_dependency_marks_failed(
    work_executor: LiveWorkItemExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    result = await work_executor.execute(_work_item("task-b", depends_on=["task-a"]))

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "missing dependency" in result.last_error
    loaded = await work_store.get("task-b")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed


@pytest.mark.asyncio
async def test_done_dependency_not_reexecuted(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "skill_a")
    _register_skill(skill_registry, "skill_b")
    calls: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    async def _skill_b(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    skill_executor.register_handler("skill_b", _skill_b)

    done_dep = _work_item("task-a", skills=["skill_a"], status=WorkItemStatus.done)
    await work_store.save(done_dep)

    result = await work_executor.execute(_work_item("task-b", skills=["skill_b"], depends_on=["task-a"]))

    assert result.status == WorkItemStatus.done
    assert calls == ["task-b"]


@pytest.mark.asyncio
async def test_failed_dependency_stops_downstream_execution(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "skill_a")
    _register_skill(skill_registry, "skill_b")
    calls: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        raise RuntimeError("dependency error")

    async def _skill_b(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    skill_executor.register_handler("skill_b", _skill_b)

    await work_store.save(_work_item("task-a", skills=["skill_a"], budget=Budget(max_attempts=1)))
    root = _work_item("task-b", skills=["skill_b"], depends_on=["task-a"], budget=Budget(max_attempts=1))

    result = await work_executor.execute(root)

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "dependency task-a failed" in result.last_error
    assert calls == ["task-a"]
    loaded_root = await work_store.get("task-b")
    assert loaded_root is not None
    assert loaded_root.status == WorkItemStatus.failed


@pytest.mark.asyncio
async def test_needs_approval_true_without_token_blocks_execution(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "safe_skill")
    calls: list[str] = []

    async def _safe_skill(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("safe_skill", _safe_skill)

    result = await work_executor.execute(
        _work_item(
            "task-approval-needed",
            skills=["safe_skill"],
            needs_approval=True,
        )
    )

    assert result.status == WorkItemStatus.blocked
    assert result.last_error is not None
    assert "explicit approval token required" in result.last_error
    assert calls == []

    loaded = await work_store.get("task-approval-needed")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.blocked


@pytest.mark.asyncio
async def test_skill_metadata_requires_approval_cannot_be_downgraded(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "sensitive_skill", requires_approval=True)
    calls: list[str] = []

    async def _sensitive_skill(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("sensitive_skill", _sensitive_skill)

    result = await work_executor.execute(
        _work_item(
            "task-sensitive",
            skills=["sensitive_skill"],
            needs_approval=False,
        )
    )

    assert result.status == WorkItemStatus.blocked
    assert calls == []
    loaded = await work_store.get("task-sensitive")
    assert loaded is not None
    assert loaded.needs_approval is True
    assert loaded.status == WorkItemStatus.blocked


@pytest.mark.asyncio
async def test_persist_failure_prevents_completion_marking(
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    _register_skill(skill_registry, "skill_a")
    store = _FailOnDoneSaveStore()
    executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=store,
    )

    async def _skill_a(_inputs: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    result = await executor.execute(_work_item("task-persist-failure", skills=["skill_a"]))

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "failed to persist completion state" in result.last_error

    loaded = await store.get("task-persist-failure")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed
