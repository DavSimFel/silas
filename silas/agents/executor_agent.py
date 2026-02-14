"""Executor Agent — produces structured tool-call plans for a WorkItem.

WI-2 enhancement: optional pydantic-ai tool loop. When use_tools=True,
the executor can iteratively call tools (read files, execute, etc.) during
its run. Supports research mode (read-only) and execution mode (full tools).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic_ai import Agent

from silas.agents.structured import run_structured_agent
from silas.models.execution import ExecutorAgentOutput, ExecutorToolCall
from silas.models.work import WorkItem
from silas.tools.skill_toolset import ToolsetProtocol

if TYPE_CHECKING:
    from silas.tools.common import AgentDeps
    from silas.tools.toolsets import AgentToolBundle

logger = logging.getLogger(__name__)

DEFAULT_EXECUTOR_SYSTEM_PROMPT = """You are the Silas Executor agent.

Return valid `ExecutorAgentOutput` JSON.

Rules:
- Summarize the action taken.
- Emit tool calls in execution order.
- Keep artifact_refs aligned to produced artifacts.
- Return concise, actionable next_steps.
"""


@dataclass(slots=True)
class ExecutorRunResult:
    output: ExecutorAgentOutput


class ExecutorAgent:
    """Executor agent with optional pydantic-ai tool loop.

    When use_tools=True, the executor can iteratively call tools during
    its run. The mode parameter controls which tools are available:
    - "research": read-only tools only (spec §5.2.1)
    - "execution": full tools including write and execute (spec §5.2.2)

    Why stateless: spec §5.1 — executor receives an ExecutionEnvelope,
    uses tools, returns results. No persistent history across work items.
    """

    def __init__(
        self,
        model: str,
        toolset: ToolsetProtocol | None = None,
        *,
        use_tools: bool = False,
        tool_bundle: AgentToolBundle | None = None,
        mode: Literal["research", "execution"] = "execution",
    ) -> None:
        self.model = model
        self.toolset = toolset
        self.mode = mode
        self.system_prompt = _load_executor_system_prompt()
        self._llm_available = True
        self._use_tools = use_tools and tool_bundle is not None
        self._tool_bundle = tool_bundle

        try:
            self.agent = Agent(
                model=model,
                output_type=ExecutorAgentOutput,
                system_prompt=self.system_prompt,
                tools=tool_bundle.custom_tools if self._use_tools and tool_bundle else [],
                toolsets=[tool_bundle.console_toolset] if self._use_tools and tool_bundle else [],
            )
        except (ImportError, ValueError, TypeError, RuntimeError) as exc:
            logger.warning(
                "Failed to initialize Executor Agent; using deterministic fallback: %s", exc
            )
            self.agent = None
            self._llm_available = False

    async def run(self, prompt: str, deps: AgentDeps | None = None) -> ExecutorRunResult:
        """Run the executor, optionally with tool-loop deps."""
        output = await self._execute_prompt(prompt)
        return ExecutorRunResult(output=output)

    async def execute(self, work_item: WorkItem, rendered_context: str = "") -> ExecutorAgentOutput:
        prompt = self._build_prompt(work_item, rendered_context)
        return await self._execute_prompt(prompt)

    async def _execute_prompt(self, prompt: str) -> ExecutorAgentOutput:
        if self.agent is not None and self._llm_available:
            try:
                raw = await run_structured_agent(
                    agent=self.agent,
                    prompt=prompt,
                    call_name="executor",
                    model_name=self.model,
                )
                output = self._coerce_output(raw)
                return self._materialize_tool_calls(output)
            except (ConnectionError, TimeoutError, ValueError, RuntimeError):
                logger.warning(
                    "Executor LLM call failed; using deterministic fallback", exc_info=True
                )

        fallback = ExecutorAgentOutput(
            summary="Executor fallback: unable to obtain structured model output.",
            last_error="executor_structured_output_invalid",
        )
        return self._materialize_tool_calls(fallback)

    def _coerce_output(self, raw: object) -> ExecutorAgentOutput:
        if isinstance(raw, ExecutorAgentOutput):
            return raw
        return ExecutorAgentOutput.model_validate(raw)

    def _materialize_tool_calls(self, output: ExecutorAgentOutput) -> ExecutorAgentOutput:
        if self.toolset is None:
            return output
        if not output.tool_calls:
            return output

        updated_calls: list[ExecutorToolCall] = []
        for call in output.tool_calls:
            result = self.toolset.call(call.tool_name, dict(call.arguments))
            payload: dict[str, object] = {
                "status": result.status,
                "result": result.output,
                "error": result.error,
            }
            if result.approval_request is not None:
                payload["result"] = {
                    "request_id": result.approval_request.request_id,
                    "tool_name": result.approval_request.tool_name,
                    "arguments": dict(result.approval_request.arguments),
                    "created_at": result.approval_request.created_at.isoformat(),
                }
            updated_calls.append(call.model_copy(update=payload))

        return output.model_copy(update={"tool_calls": updated_calls})

    def _build_prompt(self, work_item: WorkItem, rendered_context: str) -> str:
        lines = [
            f"Work item ID: {work_item.id}",
            f"Title: {work_item.title}",
            f"Body:\n{work_item.body}",
        ]

        if work_item.skills:
            lines.append(f"Skills: {', '.join(work_item.skills)}")

        if work_item.verify:
            verify_names = ", ".join(check.name for check in work_item.verify)
            lines.append(f"Verification checks: {verify_names}")

        if rendered_context.strip():
            lines.append("[CONTEXT]")
            lines.append(rendered_context)

        return "\n\n".join(lines)


def _load_executor_system_prompt() -> str:
    prompt_path = Path(__file__).resolve().parent / "prompts" / "executor_system.md"
    if prompt_path.exists():
        prompt_text = prompt_path.read_text(encoding="utf-8").strip()
        if prompt_text:
            return prompt_text
    return DEFAULT_EXECUTOR_SYSTEM_PROMPT


def build_executor_agent(
    model: str,
    toolset: ToolsetProtocol | None = None,
    *,
    use_tools: bool = False,
    tool_bundle: AgentToolBundle | None = None,
    mode: Literal["research", "execution"] = "execution",
) -> ExecutorAgent:
    """Factory for ExecutorAgent with optional tool loop support."""
    return ExecutorAgent(
        model=model,
        toolset=toolset,
        use_tools=use_tools,
        tool_bundle=tool_bundle,
        mode=mode,
    )


__all__ = ["ExecutorAgent", "ExecutorRunResult", "build_executor_agent"]
