"""Toolset assembly functions for each agent role.

Builds composite toolsets per spec §11.2 by combining pydantic-ai-backend
console tools with custom Silas tools. Each builder enforces the security
invariants for its agent role.

Why centralized builders: toolset composition is a security decision.
Agents must not assemble their own toolsets — they receive pre-built
toolsets from the runtime. This module is the single source of truth
for what tools each agent can access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic_ai import Tool
from pydantic_ai.toolsets.function import FunctionToolset

from silas.tools.backends import (
    RESEARCH_TOOL_ALLOWLIST,
    build_execution_console_toolset,
    build_readonly_console_toolset,
    build_research_console_toolset,
)
from silas.tools.common import AgentDeps, memory_search, web_search
from silas.tools.executor_tools import skill_exec
from silas.tools.planner_tools import request_research, validate_plan
from silas.tools.proxy_tools import context_inspect, tell_user


@dataclass
class AgentToolBundle:
    """Container for the two kinds of tools an agent needs.

    Why separate: pydantic-ai-backend console tools expect ConsoleDeps
    (with .backend), while our custom tools expect AgentDeps. AgentDeps
    satisfies ConsoleDeps via its backend property. The console_toolset
    is passed via Agent(toolsets=[...]) and custom tools via Agent(tools=[...]).

    This bundle lets agents receive both in a single object.
    """

    console_toolset: FunctionToolset  # type: ignore[type-arg]
    custom_tools: list[Tool[AgentDeps]]

    def all_tool_names(self) -> list[str]:
        """Return names of all tools (console + custom). For tests/logging."""
        names = list(self.console_toolset.tools.keys())
        for tool in self.custom_tools:
            name = getattr(tool, "name", None) or getattr(tool, "__name__", "unknown")
            names.append(name)
        return names


def build_proxy_toolset(deps: AgentDeps) -> AgentToolBundle:
    """Assemble proxy tools: read-only console + memory_search + web_search + tell_user + context_inspect.

    Why this composition: proxy needs to look things up (memory, web, files)
    before routing decisions, but must NEVER write or execute. Read-only
    enforcement happens at the console backend level (READONLY_RULESET).
    """
    console_toolset = build_readonly_console_toolset(deps.workspace_path)
    custom_tools = [
        _make_tool(memory_search),
        _make_tool(web_search),
        _make_tool(tell_user),
        _make_tool(context_inspect),
    ]
    return AgentToolBundle(console_toolset=console_toolset, custom_tools=custom_tools)


def build_planner_toolset(deps: AgentDeps) -> AgentToolBundle:
    """Assemble planner tools: read-only console + memory_search + request_research + validate_plan.

    Why read-only: planner reasons about plans but doesn't execute. Research
    delegation goes through the queue to executor (spec §4.3).
    """
    console_toolset = build_readonly_console_toolset(deps.workspace_path)
    custom_tools = [
        _make_tool(memory_search),
        _make_tool(request_research),
        _make_tool(validate_plan),
    ]
    return AgentToolBundle(console_toolset=console_toolset, custom_tools=custom_tools)


def build_executor_toolset(
    deps: AgentDeps,
    mode: Literal["research", "execution"],
) -> AgentToolBundle:
    """Assemble executor tools based on mode.

    Research mode: read-only tools only (RESEARCH_TOOL_ALLOWLIST clamped,
    mutation hard-disabled). Execution mode: full console + skill_exec.

    Why two modes (spec §11.1): research must be read-only to prevent side
    effects during the planning phase. Execution mode gets full power,
    gated by approval tokens at the runtime level.
    """
    if mode == "research":
        console_toolset = build_research_console_toolset(deps.workspace_path)
        custom_tools: list[Tool[AgentDeps]] = [
            _make_tool(memory_search),
            _make_tool(web_search),
        ]
    else:
        console_toolset = build_execution_console_toolset(deps.workspace_path)
        custom_tools = [
            _make_tool(memory_search),
            _make_tool(web_search),
            _make_tool(skill_exec),
        ]
    return AgentToolBundle(console_toolset=console_toolset, custom_tools=custom_tools)


def _make_tool(func: object, name: str | None = None) -> Tool[AgentDeps]:
    """Wrap an async tool function as a pydantic-ai Tool.

    Why explicit Tool wrapping: we need to collect tools into a list
    for the Agent constructor. pydantic-ai's Tool class handles
    schema generation and RunContext injection.
    """
    tool_name = name or getattr(func, "__name__", "unknown")
    return Tool(func, name=tool_name)  # type: ignore[arg-type]


def get_tool_names(bundle: AgentToolBundle) -> list[str]:
    """Extract all tool names from a bundle. Utility for tests and logging."""
    return bundle.all_tool_names()


__all__ = [
    "RESEARCH_TOOL_ALLOWLIST",
    "AgentToolBundle",
    "build_executor_toolset",
    "build_planner_toolset",
    "build_proxy_toolset",
    "get_tool_names",
]
