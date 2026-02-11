from __future__ import annotations

from typing import Protocol, runtime_checkable

from silas.models.work import WorkItem


@runtime_checkable
class SkillLoader(Protocol):
    def scan(self) -> list[object]: ...

    def load_metadata(self, skill_name: str) -> object: ...

    def load_full(self, skill_name: str) -> str: ...

    def resolve_script(self, skill_name: str, script_path: str) -> str: ...

    def validate(self, skill_name: str) -> dict[str, object]: ...

    def import_external(self, source: str, format_hint: str | None = None) -> dict[str, object]: ...


@runtime_checkable
class SkillResolver(Protocol):
    def resolve_for_work_item(self, work_item: WorkItem) -> list[object]: ...

    def prepare_toolset(
        self,
        work_item: WorkItem,
        agent_role: str,
        base_toolset: object,
        allowed_tools: list[str],
    ) -> object: ...


__all__ = ["SkillLoader", "SkillResolver"]
