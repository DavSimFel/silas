from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal, Protocol

from silas.models.skills import SkillMetadata

ToolHandler = Callable[[dict[str, object]], object]
ToolCallStatus = Literal[
    "ok",
    "error",
    "not_found",
    "filtered",
    "approval_required",
    "denied",
]


@dataclass(slots=True)
class ApprovalRequest:
    request_id: str
    tool_name: str
    arguments: dict[str, object]
    created_at: datetime


@dataclass(slots=True)
class ToolCallResult:
    status: ToolCallStatus
    output: object | None = None
    error: str | None = None
    approval_request: ApprovalRequest | None = None


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, object] = field(default_factory=dict)
    handler: ToolHandler | None = None
    requires_approval: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def call(self, arguments: dict[str, object]) -> object:
        if self.handler is None:
            return {
                "tool": self.name,
                "arguments": dict(arguments),
                "invoked_at": datetime.now(timezone.utc).isoformat(),
            }
        return self.handler(dict(arguments))


class ToolsetProtocol(Protocol):
    def list_tools(self) -> list[ToolDefinition]: ...

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult: ...


class FunctionToolset:
    """Concrete in-memory function toolset for runtime wrappers and tests."""

    def __init__(self, tools: list[ToolDefinition] | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def list_tools(self) -> list[ToolDefinition]:
        return [self._copy_tool(tool) for tool in self._tools.values()]

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolCallResult(status="not_found", error=f"unknown tool: {tool_name}")

        try:
            output = tool.call(arguments)
        except Exception as exc:  # noqa: BLE001
            return ToolCallResult(status="error", error=str(exc))
        return ToolCallResult(status="ok", output=output)

    def _copy_tool(self, tool: ToolDefinition) -> ToolDefinition:
        return ToolDefinition(
            name=tool.name,
            description=tool.description,
            input_schema=dict(tool.input_schema),
            handler=tool.handler,
            requires_approval=tool.requires_approval,
            metadata=dict(tool.metadata),
        )


class SkillToolset:
    """Inner wrapper exposing base harness tools and active work-item skill tools."""

    def __init__(
        self,
        base_toolset: ToolsetProtocol | list[ToolDefinition],
        skill_metadata: list[SkillMetadata],
        skill_tools: list[ToolDefinition] | None = None,
    ) -> None:
        self.inner = self._coerce_base_toolset(base_toolset)
        self.skill_metadata = [item.model_copy(deep=True) for item in skill_metadata]

        tool_defs = skill_tools or [self._tool_from_skill(meta) for meta in self.skill_metadata]
        self._skill_tools: dict[str, ToolDefinition] = {
            tool.name: self._copy_tool(tool)
            for tool in tool_defs
        }

    def list_tools(self) -> list[ToolDefinition]:
        seen: set[str] = set()
        tools: list[ToolDefinition] = []

        for tool in self.inner.list_tools():
            tools.append(self._copy_tool(tool))
            seen.add(tool.name)

        for tool in self._skill_tools.values():
            if tool.name in seen:
                continue
            tools.append(self._copy_tool(tool))

        return tools

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        skill_tool = self._skill_tools.get(tool_name)
        if skill_tool is not None:
            try:
                output = skill_tool.call(arguments)
            except Exception as exc:  # noqa: BLE001
                return ToolCallResult(status="error", error=str(exc))
            return ToolCallResult(status="ok", output=output)

        return self.inner.call(tool_name, arguments)

    def _tool_from_skill(self, metadata: SkillMetadata) -> ToolDefinition:
        tool_schema = dict(metadata.tool_schema)
        if not tool_schema and metadata.script_args:
            tool_schema = {
                "type": "object",
                "properties": {name: dict(schema) for name, schema in metadata.script_args.items()},
            }

        return ToolDefinition(
            name=metadata.exposed_tool_name,
            description=metadata.exposed_tool_description,
            input_schema=tool_schema,
            requires_approval=metadata.requires_approval,
            metadata={"source": "skill", "skill_name": metadata.name, **metadata.metadata},
        )

    def _copy_tool(self, tool: ToolDefinition) -> ToolDefinition:
        return ToolDefinition(
            name=tool.name,
            description=tool.description,
            input_schema=dict(tool.input_schema),
            handler=tool.handler,
            requires_approval=tool.requires_approval,
            metadata=dict(tool.metadata),
        )

    def _coerce_base_toolset(
        self,
        base_toolset: ToolsetProtocol | list[ToolDefinition],
    ) -> ToolsetProtocol:
        if isinstance(base_toolset, list):
            return FunctionToolset(base_toolset)
        return base_toolset


__all__ = [
    "ApprovalRequest",
    "FunctionToolset",
    "SkillToolset",
    "ToolCallResult",
    "ToolCallStatus",
    "ToolDefinition",
    "ToolHandler",
    "ToolsetProtocol",
]
