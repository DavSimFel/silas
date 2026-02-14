"""Planner-specific pydantic-ai tool functions.

These tools are only registered on the planner agent. They support
research delegation to executor and plan self-validation.
"""

from __future__ import annotations

import uuid

from pydantic_ai import RunContext

from silas.tools.common import AgentDeps


async def request_research(
    ctx: RunContext[AgentDeps],
    question: str,
    context: str = "",
) -> str:
    """Delegate a research question to the executor agent.

    Non-blocking: enqueues a research_request to the executor queue via
    QueueRouter and returns immediately with a request_id. The planner
    receives results via a research_result message in its next run.

    Why non-blocking: the planner's context window is expensive. Blocking
    on research would waste tokens on idle waiting. Instead, the planner
    finishes its current run and continues when results arrive (spec §4.4).
    """
    router = ctx.deps.queue_router
    if router is None:
        return "Research unavailable: no queue router configured."

    # Why import here: avoids circular import at module level.
    # QueueMessage is a Pydantic model in silas.queue.types.
    from silas.queue.types import QueueMessage

    request_id = f"research-{uuid.uuid4().hex[:12]}"
    msg = QueueMessage(
        message_kind="research_request",
        sender="planner",
        payload={
            "question": question,
            "context": context,
            "request_id": request_id,
        },
    )
    await router.route(msg)  # type: ignore[union-attr]
    return f"Research dispatched (request_id={request_id}). Result will arrive as next message."


async def validate_plan(
    ctx: RunContext[AgentDeps],
    plan_markdown: str,
) -> str:
    """Validate a plan's structure and feasibility before submitting.

    Parses the plan markdown through the plan parser to catch YAML errors,
    missing required fields, invalid budget allocations, etc. Returns
    validation results so the planner can fix issues before emitting.

    Why a tool not automatic: the planner can iteratively refine its plan
    by calling validate_plan, reading errors, and fixing them — using the
    tool loop instead of wasting a full agent re-run.
    """
    # Why deferred import: plan_parser may not exist yet in early phases.
    # Graceful degradation lets the tool be registered without hard deps.
    try:
        from silas.plan_parser import parse_plan  # type: ignore[import-not-found]

        result = parse_plan(plan_markdown)
        if result.errors:
            error_lines = "\n".join(f"- {e}" for e in result.errors)
            return f"Plan validation failed:\n{error_lines}"
        return "Plan validation passed."
    except ImportError:
        # Why fallback: plan_parser is built in a later work item.
        # Basic structural check is better than nothing.
        if "---" not in plan_markdown:
            return "Plan validation warning: no YAML front matter detected (expected '---' delimiters)."
        return "Plan validation: basic structure OK (full parser not yet available)."


__all__ = [
    "request_research",
    "validate_plan",
]
