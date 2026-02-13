from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from silas.memory.sqlite_store import SQLiteMemoryStore
from silas.models.memory import MemoryItem, MemoryType
from silas.models.skills import SkillDefinition
from silas.persistence.migrations import run_migrations
from silas.skills.executor import SkillExecutor, register_builtin_skills
from silas.skills.registry import SkillRegistry


@pytest.mark.asyncio
async def test_skill_registry_crud() -> None:
    registry = SkillRegistry()
    skill = SkillDefinition(
        name="mock_skill",
        description="test skill",
        version="1.0.0",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        requires_approval=False,
        max_retries=0,
        timeout_seconds=5,
    )

    registry.register(skill)

    assert registry.has("mock_skill")
    loaded = registry.get("mock_skill")
    assert loaded is not None
    assert loaded.name == "mock_skill"
    listed = registry.list_all()
    assert len(listed) == 1
    assert listed[0].name == "mock_skill"


@pytest.mark.asyncio
async def test_skill_executor_executes_registered_mock_skill() -> None:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="mock_skill",
            description="test skill",
            version="1.0.0",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            requires_approval=False,
            max_retries=0,
            timeout_seconds=5,
        )
    )
    executor = SkillExecutor(skill_registry=registry)

    async def _mock_handler(inputs: dict[str, object]) -> dict[str, object]:
        return {"echo": inputs.get("value")}

    executor.register_handler("mock_skill", _mock_handler)

    result = await executor.execute("mock_skill", {"value": "ok"})
    assert result.success is True
    assert result.error is None
    assert result.output == {"echo": "ok"}
    assert result.retries_used == 0


@pytest.mark.asyncio
async def test_skill_executor_timeout_enforced() -> None:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="slow_skill",
            description="slow test skill",
            version="1.0.0",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            requires_approval=False,
            max_retries=0,
            timeout_seconds=1,
        )
    )
    executor = SkillExecutor(skill_registry=registry)

    async def _slow_handler(inputs: dict[str, object]) -> dict[str, object]:
        del inputs
        await asyncio.sleep(1.2)
        return {"done": True}

    executor.register_handler("slow_skill", _slow_handler)

    result = await executor.execute("slow_skill", {})
    assert result.success is False
    assert result.error is not None
    assert "timed out" in result.error
    assert result.retries_used == 0


@pytest.mark.asyncio
async def test_skill_executor_retry_with_exponential_backoff() -> None:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="flaky_skill",
            description="retry test skill",
            version="1.0.0",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            requires_approval=False,
            max_retries=2,
            timeout_seconds=2,
        )
    )
    executor = SkillExecutor(skill_registry=registry)
    executor._backoff_base_seconds = 0

    attempts = {"count": 0}

    async def _flaky_handler(inputs: dict[str, object]) -> dict[str, object]:
        del inputs
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary failure")
        return {"ok": True}

    executor.register_handler("flaky_skill", _flaky_handler)

    result = await executor.execute("flaky_skill", {})
    assert result.success is True
    assert result.output == {"ok": True}
    assert result.retries_used == 2
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_builtin_memory_recall_with_sqlite_memory_store(tmp_path: Path) -> None:
    db_path = tmp_path / "skills.db"
    await run_migrations(str(db_path))
    memory_store = SQLiteMemoryStore(str(db_path))

    now = datetime.now(UTC)
    await memory_store.store(
        MemoryItem(
            memory_id="mem-skill-1",
            content="Roadmap planning note for Q1 launch.",
            memory_type=MemoryType.fact,
            created_at=now,
            updated_at=now,
            source_kind="test",
        )
    )
    await memory_store.store(
        MemoryItem(
            memory_id="mem-skill-2",
            content="Unrelated shopping list.",
            memory_type=MemoryType.fact,
            created_at=now,
            updated_at=now,
            source_kind="test",
        )
    )

    registry = SkillRegistry()
    register_builtin_skills(registry)
    executor = SkillExecutor(skill_registry=registry, memory_store=memory_store)

    result = await executor.execute("memory_recall", {"query": "Roadmap", "limit": 5})

    assert result.success is True
    rows = result.output.get("results")
    assert isinstance(rows, list)
    memory_ids = {row["memory_id"] for row in rows if isinstance(row, dict)}
    assert "mem-skill-1" in memory_ids
    assert "mem-skill-2" not in memory_ids
