from __future__ import annotations

from collections.abc import Callable

from silas.models.skills import SkillMetadata
from silas.models.work import WorkItem
from silas.skills.registry import SilasSkillLoader as SkillLoader
from silas.tools.approval_required import ApprovalRequiredToolset
from silas.tools.filtered import FilteredToolset
from silas.tools.prepared import PreparedToolset
from silas.tools.skill_toolset import SkillToolset, ToolDefinition, ToolsetProtocol


class LiveSkillResolver:
    """Resolves skill metadata and composes the canonical toolset wrapper chain."""

    def __init__(
        self,
        skill_loader: SkillLoader,
        work_item_lookup: Callable[[str], WorkItem | None] | None = None,
    ) -> None:
        self._skill_loader = skill_loader
        self._work_item_lookup = work_item_lookup

    def resolve_for_work_item(self, work_item: WorkItem) -> list[SkillMetadata]:
        skill_names = self._resolve_skill_names(work_item)
        metadata: list[SkillMetadata] = []

        for skill_name in skill_names:
            loaded = self._skill_loader.load_metadata(skill_name)
            if isinstance(loaded, SkillMetadata):
                metadata.append(loaded.model_copy(deep=True))
            elif isinstance(loaded, dict):
                metadata.append(SkillMetadata.model_validate(loaded))
            else:
                raise TypeError(
                    f"skill_loader.load_metadata({skill_name!r}) returned unsupported type "
                    f"{type(loaded).__name__}"
                )

        return metadata

    def prepare_toolset(
        self,
        work_item: WorkItem,
        agent_role: str,
        base_toolset: ToolsetProtocol | list[ToolDefinition],
        allowed_tools: list[str],
    ) -> ApprovalRequiredToolset:
        skill_metadata = self.resolve_for_work_item(work_item)

        skill_toolset = SkillToolset(base_toolset=base_toolset, skill_metadata=skill_metadata)
        prepared_toolset = PreparedToolset(inner=skill_toolset, agent_role=agent_role)
        filtered_toolset = FilteredToolset(inner=prepared_toolset, allowed_tools=allowed_tools)
        return ApprovalRequiredToolset(inner=filtered_toolset)

    def _resolve_skill_names(self, work_item: WorkItem) -> list[str]:
        if work_item.skills:
            return self._dedupe(work_item.skills)

        if not work_item.parent or self._work_item_lookup is None:
            return []

        visited: set[str] = {work_item.id}
        parent_id: str | None = work_item.parent

        while parent_id is not None:
            if parent_id in visited:
                break
            visited.add(parent_id)

            parent = self._work_item_lookup(parent_id)
            if parent is None:
                break

            if parent.skills:
                return self._dedupe(parent.skills)

            parent_id = parent.parent

        return []

    def _dedupe(self, names: list[str]) -> list[str]:
        seen: set[str] = set()
        resolved: list[str] = []
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            resolved.append(name)
        return resolved


SkillResolver = LiveSkillResolver


__all__ = ["LiveSkillResolver", "SkillResolver"]
