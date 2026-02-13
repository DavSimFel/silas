"""SkillResolver — resolves work item skill lists to loaded metadata and builds toolsets.

The resolver bridges the gap between a WorkItem's declared skill names and the
actual SkillMetadata + toolset wrappers needed at execution time. It handles
skill inheritance (child tasks inherit parent skills when their own list is empty)
and constructs the canonical toolset wrapper chain:

    SkillToolset → PreparedToolset → FilteredToolset → ApprovalRequiredToolset
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from silas.models.skills import SkillMetadata
from silas.models.work import WorkItem
from silas.skills.loader import SilasSkillLoader
from silas.tools.skill_toolset import (
    SkillToolset,
    ToolCallResult,
    ToolDefinition,
    ToolsetProtocol,
)

logger = logging.getLogger(__name__)


class PreparedToolset:
    """Wraps a toolset with role-based preparation (e.g. injecting role context).

    Currently a thin pass-through — exists so the wrapper chain is explicit
    and role-based restrictions can be added later without restructuring.
    """

    def __init__(self, inner: ToolsetProtocol, agent_role: str) -> None:
        self._inner = inner
        self.agent_role = agent_role

    def list_tools(self) -> list[ToolDefinition]:
        return self._inner.list_tools()

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        return self._inner.call(tool_name, arguments)


class FilteredToolset:
    """Only exposes tools in the allowed_tools list. Blocks everything else.

    This is the access-control boundary: even if a tool is registered,
    the agent can't call it unless it's in the allowlist.
    """

    def __init__(self, inner: ToolsetProtocol, allowed_tools: list[str]) -> None:
        self._inner = inner
        # Use a set for O(1) lookup on every call
        self._allowed: set[str] = set(allowed_tools)

    def list_tools(self) -> list[ToolDefinition]:
        return [t for t in self._inner.list_tools() if t.name in self._allowed]

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        if tool_name not in self._allowed:
            return ToolCallResult(status="filtered", error=f"tool '{tool_name}' not in allowlist")
        return self._inner.call(tool_name, arguments)


class ApprovalRequiredToolset:
    """Intercepts calls to tools that require approval, returning approval_required status.

    Tools with requires_approval=True won't execute directly — instead they
    return an approval request that must be granted before re-invocation.
    """

    def __init__(self, inner: ToolsetProtocol) -> None:
        self._inner = inner
        # Cache which tools need approval so we don't re-scan every call
        self._approval_required: set[str] = {
            t.name for t in inner.list_tools() if t.requires_approval
        }

    def list_tools(self) -> list[ToolDefinition]:
        return self._inner.list_tools()

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        if tool_name in self._approval_required:
            return ToolCallResult(
                status="approval_required",
                error=f"tool '{tool_name}' requires approval before execution",
            )
        return self._inner.call(tool_name, arguments)


@dataclass(slots=True)
class ResolvedSkills:
    """Container for the resolution result — metadata + names actually found."""

    metadata: list[SkillMetadata] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class SkillResolver:
    """Resolves skill names to metadata and builds execution toolsets.

    Args:
        loader: The skill loader to fetch metadata from disk.
        parent_resolver: Optional callable that returns the parent WorkItem's skill names.
                         Used for inheritance when a child task has no skills declared.
    """

    def __init__(
        self,
        loader: SilasSkillLoader,
        parent_resolver: ParentSkillResolver | None = None,
    ) -> None:
        self._loader = loader
        self._parent_resolver = parent_resolver

    def resolve_for_work_item(self, work_item: WorkItem) -> list[SkillMetadata]:
        """Resolve the work item's skill names to loaded SkillMetadata.

        If the work item has no skills declared and a parent_resolver is available,
        inherits the parent's skills. Missing skills are logged but don't fail
        the resolution — partial results are returned.
        """
        skill_names = list(work_item.skills) if work_item.skills else []

        # Inheritance: if no skills declared, try parent
        if not skill_names and self._parent_resolver is not None:
            skill_names = self._parent_resolver(work_item)

        resolved = self._resolve_names(skill_names)

        if resolved.missing:
            logger.warning(
                "Work item %s: missing skills %s",
                work_item.id,
                resolved.missing,
            )

        return resolved.metadata

    def prepare_toolset(
        self,
        work_item: WorkItem,
        agent_role: str,
        base_toolset: ToolsetProtocol,
        allowed_tools: list[str],
    ) -> ApprovalRequiredToolset:
        """Build the canonical wrapper chain for work item execution.

        Chain: SkillToolset → PreparedToolset → FilteredToolset → ApprovalRequiredToolset

        Args:
            work_item: The work item being executed.
            agent_role: Role of the executing agent (e.g. "executor", "planner").
            base_toolset: Core harness tools available to all agents.
            allowed_tools: Explicit allowlist of tool names the agent may call.

        Returns:
            The outermost toolset wrapper, ready for agent use.
        """
        # Step 1: Resolve skills to metadata
        skill_metadata = self.resolve_for_work_item(work_item)

        # Step 2: Inner layer — merge base tools with skill-specific tools
        skill_toolset = SkillToolset(
            base_toolset=base_toolset,
            skill_metadata=skill_metadata,
        )

        # Step 3: Role preparation (currently pass-through, extensible later)
        prepared = PreparedToolset(inner=skill_toolset, agent_role=agent_role)

        # Step 4: Filter to only allowed tools
        filtered = FilteredToolset(inner=prepared, allowed_tools=allowed_tools)

        # Step 5: Intercept approval-required tools
        return ApprovalRequiredToolset(inner=filtered)

    def _resolve_names(self, skill_names: list[str]) -> ResolvedSkills:
        """Load metadata for each skill name, tracking missing ones."""
        metadata: list[SkillMetadata] = []
        missing: list[str] = []

        for name in skill_names:
            try:
                meta = self._loader.load_metadata(name)
                metadata.append(meta)
            except ValueError:
                missing.append(name)

        return ResolvedSkills(metadata=metadata, missing=missing)


from collections.abc import Callable  # noqa: E402

# Type alias: given a work item, return the parent's skill name list
ParentSkillResolver = Callable[[WorkItem], list[str]]


__all__ = [
    "ApprovalRequiredToolset",
    "FilteredToolset",
    "PreparedToolset",
    "ResolvedSkills",
    "SkillResolver",
]
