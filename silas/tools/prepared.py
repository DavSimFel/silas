from __future__ import annotations

from silas.tools.skill_toolset import ToolCallResult, ToolDefinition, ToolsetProtocol


class PreparedToolset:
    """Adds deterministic role-specific metadata to tool definitions."""

    def __init__(self, inner: ToolsetProtocol, agent_role: str) -> None:
        self.inner = inner
        self.agent_role = agent_role

    def list_tools(self) -> list[ToolDefinition]:
        return [self._prepare_tool(tool) for tool in self.inner.list_tools()]

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        return self.inner.call(tool_name, arguments)

    def _prepare_tool(self, tool: ToolDefinition) -> ToolDefinition:
        schema = dict(tool.input_schema)
        schema["x-silas-agent-role"] = self.agent_role

        metadata = dict(tool.metadata)
        metadata["prepared_for_role"] = self.agent_role

        return ToolDefinition(
            name=tool.name,
            description=f"{tool.description}\n[Prepared for role: {self.agent_role}]",
            input_schema=schema,
            handler=tool.handler,
            requires_approval=tool.requires_approval,
            metadata=metadata,
        )


__all__ = ["PreparedToolset"]
