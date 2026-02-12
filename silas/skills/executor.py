from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import TaintLevel
from silas.models.skills import SkillDefinition, SkillResult
from silas.models.work import WorkItem
from silas.protocols.memory import MemoryStore
from silas.skills.registry import SkillRegistry

type SkillHandler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class SkillExecutor:
    def __init__(
        self,
        skill_registry: SkillRegistry,
        memory_store: MemoryStore | None = None,
        work_item: WorkItem | None = None,
    ) -> None:
        self._skill_registry = skill_registry
        self._memory_store = memory_store
        self._work_item = work_item
        self._handlers: dict[str, SkillHandler] = {}
        self._backoff_base_seconds = 0.1

        self.register_handler("web_search", self._run_web_search)
        self.register_handler("memory_store", self._run_memory_store)
        self.register_handler("memory_recall", self._run_memory_recall)

    def set_work_item(self, work_item: WorkItem | None) -> None:
        self._work_item = work_item

    def register_handler(self, skill_name: str, handler: SkillHandler) -> None:
        self._handlers[skill_name] = handler

    def skill_requires_approval(self, skill_name: str) -> bool:
        """Return whether skill metadata requires explicit approval at execution time."""
        definition = self._skill_registry.get(skill_name)
        return bool(definition is not None and definition.requires_approval)

    async def execute(self, skill_name: str, inputs: dict[str, object]) -> SkillResult:
        started_at = datetime.now(UTC)
        definition = self._skill_registry.get(skill_name)
        if definition is None:
            return SkillResult(
                skill_name=skill_name,
                success=False,
                output={},
                error=f"skill '{skill_name}' is not registered",
                duration_ms=self._duration_ms(started_at),
                retries_used=0,
            )

        if definition.requires_approval and not self._has_approval():
            return SkillResult(
                skill_name=skill_name,
                success=False,
                output={},
                error=f"skill '{skill_name}' requires approval",
                duration_ms=self._duration_ms(started_at),
                retries_used=0,
            )

        handler = self._handlers.get(skill_name)
        if handler is None:
            return SkillResult(
                skill_name=skill_name,
                success=False,
                output={},
                error=f"no executor handler for skill '{skill_name}'",
                duration_ms=self._duration_ms(started_at),
                retries_used=0,
            )

        retries_used = 0
        last_error: str | None = None
        max_attempts = definition.max_retries + 1

        for attempt in range(max_attempts):
            try:
                output = await asyncio.wait_for(
                    handler(dict(inputs)),
                    timeout=definition.timeout_seconds,
                )
                return SkillResult(
                    skill_name=skill_name,
                    success=True,
                    output=output,
                    error=None,
                    duration_ms=self._duration_ms(started_at),
                    retries_used=retries_used,
                )
            except TimeoutError:
                last_error = (
                    f"skill '{skill_name}' timed out after {definition.timeout_seconds} seconds"
                )
            except (ValueError, TypeError, RuntimeError, OSError) as exc:
                last_error = str(exc)

            if attempt < definition.max_retries:
                retries_used += 1
                await asyncio.sleep(self._backoff_seconds(retries_used))

        return SkillResult(
            skill_name=skill_name,
            success=False,
            output={},
            error=last_error or f"skill '{skill_name}' failed",
            duration_ms=self._duration_ms(started_at),
            retries_used=retries_used,
        )

    def _duration_ms(self, started_at: datetime) -> int:
        ended_at = datetime.now(UTC)
        delta = ended_at - started_at
        return int(delta.total_seconds() * 1000)

    def _backoff_seconds(self, retry_number: int) -> float:
        return self._backoff_base_seconds * (2 ** max(retry_number - 1, 0))

    def _has_approval(self) -> bool:
        work_item = self._work_item
        if work_item is None:
            return False
        if not work_item.needs_approval:
            return True
        return work_item.approval_token is not None

    async def _run_web_search(self, inputs: dict[str, object]) -> dict[str, object]:
        query = self._required_str(inputs, "query")
        limit = self._resolve_limit(inputs.get("limit"), default=5)

        results = [
            {
                "title": f"Mock result {idx} for {query}",
                "url": f"https://example.com/search/{idx}",
                "snippet": f"Mock web_search snippet {idx} for query '{query}'.",
            }
            for idx in range(1, limit + 1)
        ]
        return {"results": results}

    async def _run_memory_store(self, inputs: dict[str, object]) -> dict[str, object]:
        if self._memory_store is None:
            raise RuntimeError("memory_store is not configured")

        content = self._required_str(inputs, "content")
        memory_type_raw = self._required_str(inputs, "memory_type")
        memory_type = MemoryType(memory_type_raw)
        now = datetime.now(UTC)
        memory_id = f"skill:{memory_type.value}:{uuid.uuid4().hex}"

        await self._memory_store.store(
            MemoryItem(
                memory_id=memory_id,
                content=content,
                memory_type=memory_type,
                taint=TaintLevel.owner,
                created_at=now,
                updated_at=now,
                source_kind="skill:memory_store",
            )
        )
        return {
            "memory_id": memory_id,
            "memory_type": memory_type.value,
            "stored_at": now.isoformat(),
        }

    async def _run_memory_recall(self, inputs: dict[str, object]) -> dict[str, object]:
        if self._memory_store is None:
            raise RuntimeError("memory_store is not configured")

        query = self._required_str(inputs, "query")
        limit = self._resolve_limit(inputs.get("limit"), default=5)
        items = await self._memory_store.search_keyword(query, limit=limit)
        return {
            "results": [item.model_dump(mode="json") for item in items],
        }

    def _required_str(self, inputs: dict[str, object], field_name: str) -> str:
        value = inputs.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"'{field_name}' must be a non-empty string")
        return value

    def _resolve_limit(self, value: object, default: int) -> int:
        if value is None:
            return default
        if not isinstance(value, int):
            raise ValueError("'limit' must be an integer")
        if value <= 0:
            raise ValueError("'limit' must be > 0")
        return value


def builtin_skill_definitions() -> list[SkillDefinition]:
    return [
        SkillDefinition(
            name="web_search",
            description="Retrieve web results for a search query.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["query"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {"type": "object"},
                    }
                },
            },
            requires_approval=False,
            max_retries=1,
            timeout_seconds=10,
        ),
        SkillDefinition(
            name="memory_store",
            description="Store an item in long-term memory.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "memory_type": {"type": "string"},
                },
                "required": ["content", "memory_type"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "memory_type": {"type": "string"},
                    "stored_at": {"type": "string"},
                },
            },
            requires_approval=True,
            max_retries=1,
            timeout_seconds=15,
        ),
        SkillDefinition(
            name="memory_recall",
            description="Recall relevant memories by keyword search.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["query"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {"type": "object"},
                    }
                },
            },
            requires_approval=False,
            max_retries=1,
            timeout_seconds=10,
        ),
    ]


def register_builtin_skills(skill_registry: SkillRegistry) -> None:
    for definition in builtin_skill_definitions():
        skill_registry.register(definition)


__all__ = [
    "SkillExecutor",
    "SkillHandler",
    "builtin_skill_definitions",
    "register_builtin_skills",
]
