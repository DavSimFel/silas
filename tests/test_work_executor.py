from __future__ import annotations

import pytest
from silas.models.skills import SkillDefinition
from silas.models.work import Budget, WorkItem, WorkItemStatus, WorkItemType
from silas.skills.executor import SkillExecutor
from silas.skills.registry import SkillRegistry
from silas.work.executor import LiveWorkItemExecutor

from tests.fakes import InMemoryWorkItemStore


def _register_skill(registry: SkillRegistry, name: str) -> None:
    registry.register(
        SkillDefinition(
            name=name,
            description=f"{name} test skill",
            version="1.0.0",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            requires_approval=False,
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
