from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from silas.models.agents import AgentResponse, InteractionMode, InteractionRegister, RouteDecision


class StructuredRunnable(Protocol):
    async def run(self, prompt: str) -> object: ...


def summarize_validation_error(err: ValidationError) -> str:
    parts: list[str] = []
    for issue in err.errors():
        location = ".".join(str(piece) for piece in issue.get("loc", []))
        message = str(issue.get("msg", "validation error"))
        if location:
            parts.append(f"{location}: {message}")
        else:
            parts.append(message)
    return "\n".join(parts) if parts else str(err)


def _unwrap_run_result(result: object) -> object:
    try:
        return result.output  # type: ignore[attr-defined]
    except AttributeError:
        return result


def structured_fallback(call_name: str, default_context_profile: str) -> object:
    if call_name == "proxy":
        return RouteDecision(
            route="direct",
            reason="proxy structured output invalid; fallback response",
            response=AgentResponse(
                message=(
                    "I hit a structured-output error while routing your request. "
                    "Please try again with a shorter prompt."
                ),
                needs_approval=False,
            ),
            interaction_register=InteractionRegister.status,
            interaction_mode=InteractionMode.confirm_only_when_required,
            context_profile=default_context_profile,
        )
    if call_name == "planner":
        return AgentResponse(
            message="I could not produce a valid plan structure for this request.",
            plan_action=None,
            needs_approval=False,
        )
    if call_name == "executor":
        from silas.models.execution import ExecutorAgentOutput

        return ExecutorAgentOutput(
            summary="Executor structured output invalid.",
            last_error="executor_structured_output_invalid",
        )
    raise RuntimeError(f"No structured fallback configured for call_name={call_name!r}")


async def run_structured_agent(
    agent: StructuredRunnable,
    prompt: str,
    call_name: str,
    default_context_profile: str = "conversation",
) -> object:
    try:
        first = await agent.run(prompt)
        return _unwrap_run_result(first)
    except ValidationError as err:
        repair_prompt = f"{prompt}\n\n[SCHEMA VALIDATION ERROR]\n{summarize_validation_error(err)}"
        try:
            second = await agent.run(repair_prompt)
            return _unwrap_run_result(second)
        except ValidationError:
            return structured_fallback(call_name, default_context_profile)


__all__ = ["run_structured_agent", "structured_fallback", "summarize_validation_error"]
