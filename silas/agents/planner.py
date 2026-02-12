"""Planner Agent — generates executable markdown plans for complex requests."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent

from silas.agents.structured import run_structured_agent
from silas.models.agents import AgentResponse, InteractionMode, PlanAction, PlanActionType

logger = logging.getLogger(__name__)

DEFAULT_PLANNER_SYSTEM_PROMPT = """You are the Silas Planner agent.

Return a valid AgentResponse with a plan_action that includes markdown plan text.

Requirements:
- Always produce parseable markdown with YAML front matter.
- YAML must include: id, type, title.
- Keep the body actionable and concise.
- Set needs_approval=true for executable plans.
- Use interaction_mode=act_and_report unless the task is clearly high-risk.
"""


@dataclass(slots=True)
class PlannerRunResult:
    output: AgentResponse


class PlannerAgent:
    def __init__(self, model: str, default_context_profile: str = "planning") -> None:
        self.model = model
        self.default_context_profile = default_context_profile
        self.system_prompt = _load_planner_system_prompt()
        self._llm_available = True

        try:
            self.agent = Agent(
                model=model,
                output_type=AgentResponse,
                system_prompt=self.system_prompt,
            )
        except Exception:
            logger.warning("Failed to initialize Planner Agent; falling back to deterministic planner")
            self.agent = None
            self._llm_available = False

    async def run(self, prompt: str) -> PlannerRunResult:
        response = await self.plan(prompt)
        return PlannerRunResult(output=response)

    async def plan(self, user_request: str, rendered_context: str = "") -> AgentResponse:
        prompt = self._build_prompt(user_request, rendered_context)

        if self.agent is not None and self._llm_available:
            try:
                raw = await run_structured_agent(
                    agent=self.agent,
                    prompt=prompt,
                    call_name="planner",
                    default_context_profile=self.default_context_profile,
                )
                return self._coerce_response(raw, user_request)
            except Exception:
                logger.warning("Planner LLM call failed; using deterministic fallback")

        return self._fallback_response(user_request)

    def _coerce_response(self, raw: object, user_request: str) -> AgentResponse:
        if isinstance(raw, AgentResponse):
            response = raw
        else:
            response = AgentResponse.model_validate(raw)
        return self._ensure_plan_markdown(response, user_request)

    def _ensure_plan_markdown(self, response: AgentResponse, user_request: str) -> AgentResponse:
        plan_action = response.plan_action
        if plan_action is not None and plan_action.plan_markdown and plan_action.plan_markdown.strip():
            return response

        fallback_markdown = self._fallback_markdown(user_request)
        patched_action = PlanAction(
            action=PlanActionType.propose,
            plan_markdown=fallback_markdown,
            interaction_mode_override=InteractionMode.act_and_report,
        )
        return response.model_copy(
            update={
                "message": response.message or "Generated an executable fallback plan.",
                "plan_action": patched_action,
                "needs_approval": True,
            }
        )

    def _fallback_response(self, user_request: str) -> AgentResponse:
        return AgentResponse(
            message="Generated a deterministic fallback plan.",
            plan_action=PlanAction(
                action=PlanActionType.propose,
                plan_markdown=self._fallback_markdown(user_request),
                interaction_mode_override=InteractionMode.act_and_report,
            ),
            needs_approval=True,
        )

    def _build_prompt(self, user_request: str, rendered_context: str) -> str:
        if not rendered_context.strip():
            return user_request
        return (
            "[CONTEXT]\n"
            f"{rendered_context}\n\n"
            "[USER REQUEST]\n"
            f"{user_request}"
        )

    def _fallback_markdown(self, user_request: str) -> str:
        request_text = " ".join(user_request.strip().split()) or "No request provided."
        title = request_text[:60] if request_text else "Execute user request"
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        return (
            "---\n"
            f"id: {task_id}\n"
            "type: task\n"
            f"title: {title}\n"
            "interaction_mode: act_and_report\n"
            "skills: []\n"
            "on_stuck: consult_planner\n"
            "---\n\n"
            "# Context\n"
            f"User request: {request_text}\n\n"
            "# What to do\n"
            "1. Break the request into concrete steps.\n"
            "2. Execute the steps in sequence and capture artifacts.\n"
            "3. Verify the final result before reporting completion.\n\n"
            "# Constraints\n"
            "- Stay within repository and task boundaries.\n"
            "- Avoid destructive operations unless explicitly requested.\n\n"
            "# If you get stuck\n"
            "- Report the blocker and request the minimum clarification needed.\n"
        )


def _load_planner_system_prompt() -> str:
    prompt_path = Path(__file__).resolve().parent / "prompts" / "planner_system.md"
    if prompt_path.exists():
        prompt_text = prompt_path.read_text(encoding="utf-8").strip()
        if prompt_text:
            return prompt_text
    return DEFAULT_PLANNER_SYSTEM_PROMPT


def build_planner_agent(model: str, default_context_profile: str = "planning") -> PlannerAgent:
    return PlannerAgent(model=model, default_context_profile=default_context_profile)


__all__ = ["PlannerAgent", "PlannerRunResult", "build_planner_agent"]
