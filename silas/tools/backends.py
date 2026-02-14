"""Console toolset factories wrapping pydantic-ai-backend.

Provides thin factory functions that create pre-configured FunctionToolset
instances for each agent role. These are the innermost layer in the toolset
pipeline (spec §11.2).

Why wrap instead of using create_console_toolset directly: we need to
control which tools are exposed per agent role (e.g., proxy gets read-only,
executor-research gets a clamped allowlist). The wrapper centralizes this
policy so agents don't make their own security decisions.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai_backends import ConsoleDeps
from pydantic_ai_backends.permissions import READONLY_RULESET
from pydantic_ai_backends.toolsets import create_console_toolset

# Why a module-level constant matching the spec §5.2.1: this is the
# security boundary for research mode. Tools not in this set are
# structurally absent from the toolset — not just prompt-filtered.
RESEARCH_TOOL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "read_file",
        "grep",
        "glob",
        "ls",
        "web_search",
        "memory_search",
    }
)

# Console tools that involve filesystem mutation or code execution.
# Used to strip dangerous tools from read-only toolsets.
_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "execute",
    }
)


def build_readonly_console_toolset(workspace: Path) -> FunctionToolset[ConsoleDeps]:
    """Create a read-only console toolset for proxy/planner agents.

    Uses READONLY_RULESET permissions and disables execute. Mutation tools
    (write_file, edit_file) are included in the toolset but blocked at
    runtime by the permission checker — belt-and-suspenders with our
    FilteredToolset layer on top.
    """
    return create_console_toolset(
        include_execute=False,
        permissions=READONLY_RULESET,
    )


def build_research_console_toolset(workspace: Path) -> FunctionToolset[ConsoleDeps]:
    """Create a research-mode console toolset for executor in research mode.

    Only exposes tools in RESEARCH_TOOL_ALLOWLIST that are also console tools.
    Mutation tools are structurally removed — not just filtered.
    """
    toolset = create_console_toolset(
        include_execute=False,
        permissions=READONLY_RULESET,
    )
    # Why structural removal: prompt-level filtering is bypassable by
    # sufficiently clever model output. Removing tools from the toolset
    # means the model literally cannot call them.
    _remove_tools_not_in_allowlist(toolset, RESEARCH_TOOL_ALLOWLIST)
    return toolset


def build_execution_console_toolset(workspace: Path) -> FunctionToolset[ConsoleDeps]:
    """Create a full console toolset for executor in execution mode.

    Includes all tools: read, write, edit, execute. Security enforcement
    happens via the outer FilteredToolset and ApprovalRequiredToolset layers.
    """
    return create_console_toolset(
        include_execute=True,
    )


def _remove_tools_not_in_allowlist(
    toolset: FunctionToolset[ConsoleDeps],
    allowlist: frozenset[str],
) -> None:
    """Remove tools from a FunctionToolset that aren't in the allowlist.

    Mutates the toolset in-place. Only considers console-level tool names
    (custom tools like web_search/memory_search are added separately).
    """
    to_remove = [name for name in toolset.tools if name not in allowlist]
    for name in to_remove:
        del toolset.tools[name]


__all__ = [
    "RESEARCH_TOOL_ALLOWLIST",
    "build_execution_console_toolset",
    "build_readonly_console_toolset",
    "build_research_console_toolset",
]
