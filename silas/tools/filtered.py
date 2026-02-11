from __future__ import annotations

from silas.tools.skill_toolset import ToolCallResult, ToolDefinition, ToolsetProtocol


class FilteredToolset:
    """Filters tools to an allowlist before model exposure and execution."""

    def __init__(self, inner: ToolsetProtocol, allowed_tools: list[str] | None) -> None:
        self.inner = inner
        self.allowed_tools = list(allowed_tools or [])
        self._allowed_set = set(self.allowed_tools)

    def list_tools(self) -> list[ToolDefinition]:
        if not self._allowed_set:
            return []
        return [tool for tool in self.inner.list_tools() if tool.name in self._allowed_set]

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        if tool_name not in self._allowed_set:
            return ToolCallResult(
                status="filtered",
                error=f"tool '{tool_name}' is not allowed in this scope",
            )
        return self.inner.call(tool_name, arguments)


__all__ = ["FilteredToolset"]
