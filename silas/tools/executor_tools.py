"""Executor-specific pydantic-ai tool functions.

These tools are only registered on the executor agent in execution mode.
Research mode gets a restricted subset via the toolset builder.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from silas.tools.common import AgentDeps


async def skill_exec(
    ctx: RunContext[AgentDeps],
    skill_name: str,
    args: dict[str, object],
) -> str:
    """Execute a registered skill by name.

    Wraps the existing skill resolver to find and invoke a skill. Only
    available in execution mode — structurally absent in research mode
    (spec §11.1).

    Why a tool wrapper: the pydantic-ai agent loop can iteratively call
    skills, inspect results, and decide next steps — enabling multi-step
    execution within a single attempt.
    """
    # Why stub: skill resolver integration comes in a later work item.
    # The tool is registered so agents can be tested with it and the
    # toolset composition is correct.
    return (
        f"Skill '{skill_name}' execution placeholder. "
        f"Args: {args}. Full resolver integration pending."
    )


__all__ = [
    "skill_exec",
]
