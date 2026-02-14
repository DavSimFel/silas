from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from silas.core.metrics import LLM_CALLS_TOTAL, LLM_TOKENS_TOTAL
from silas.core.telemetry import get_tracer
from silas.models.agents import AgentResponse, InteractionMode, InteractionRegister, RouteDecision

_TRACER = get_tracer("silas.agents")


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
    """Extract .output from pydantic-ai RunResult, or return as-is."""
    output = getattr(result, "output", None)
    return output if output is not None else result


def _record_usage(result: object, model_name: str) -> None:
    usage_fn = getattr(result, "usage", None)
    if not callable(usage_fn):
        return
    usage = usage_fn()
    request_tokens = getattr(usage, "request_tokens", None)
    response_tokens = getattr(usage, "response_tokens", None)
    if request_tokens is not None:
        LLM_TOKENS_TOTAL.labels(model=model_name, direction="input").inc(request_tokens)
    if response_tokens is not None:
        LLM_TOKENS_TOTAL.labels(model=model_name, direction="output").inc(response_tokens)


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
    model_name: str = "unknown",
) -> object:
    try:
        with _TRACER.start_as_current_span(f"agent.{call_name}"):
            LLM_CALLS_TOTAL.labels(model=model_name).inc()
            first = await agent.run(prompt)
            _record_usage(first, model_name)
        return _unwrap_run_result(first)
    except ValidationError as err:
        repair_prompt = f"{prompt}\n\n[SCHEMA VALIDATION ERROR]\n{summarize_validation_error(err)}"
        try:
            with _TRACER.start_as_current_span(f"agent.{call_name}"):
                LLM_CALLS_TOTAL.labels(model=model_name).inc()
                second = await agent.run(repair_prompt)
                _record_usage(second, model_name)
            return _unwrap_run_result(second)
        except ValidationError:
            return structured_fallback(call_name, default_context_profile)


__all__ = ["run_structured_agent", "structured_fallback", "summarize_validation_error"]
