"""Proxy Agent — routes messages as direct or planner.

Phase 1a: Attempts LLM call via PydanticAI Agent. Falls back to
deterministic direct routing if the LLM call fails or no API key
is configured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent

from silas.models.agents import AgentResponse, InteractionMode, InteractionRegister, RouteDecision

logger = logging.getLogger(__name__)

DEFAULT_PROXY_SYSTEM_PROMPT = """You are the Silas Proxy agent.

Return a valid RouteDecision object for every request.

Routing criteria:
- route="direct": simple questions, greetings, factual lookups, and single-step tasks.
- route="planner": multi-step tasks, tasks requiring tools/skills, or tasks with dependencies.

Output contract:
- direct route: set response.message with the user-facing answer.
- planner route: set response to null; planner will produce plan actions.
- always set reason, interaction_register, interaction_mode, and context_profile.

Context profile guidance:
- conversation: general dialogue and simple Q&A
- coding: code/debug/implementation tasks
- research: investigation and source-heavy lookups
- support: troubleshooting and helpdesk-style requests
- planning: explicit multi-step orchestration requests
"""


@dataclass(slots=True)
class ProxyRunResult:
    output: RouteDecision


class ProxyAgent:
    """Phase 1a proxy wrapper.

    Wraps a PydanticAI Agent with RouteDecision output type.
    Attempts real LLM routing; falls back to deterministic echo if unavailable.
    """

    def __init__(self, model: str, default_context_profile: str = "conversation") -> None:
        self.model = model
        self.default_context_profile = default_context_profile
        self.system_prompt = _load_proxy_system_prompt()
        self._llm_available = True

        try:
            self.agent = Agent(
                model=model,
                output_type=RouteDecision,
                system_prompt=self.system_prompt,
            )
        except Exception:
            logger.warning("Failed to initialize PydanticAI Agent for proxy — using fallback mode", exc_info=True)
            self.agent = None
            self._llm_available = False

    async def run(self, prompt: str) -> ProxyRunResult:
        # Try LLM path first
        if self.agent is not None and self._llm_available:
            try:
                result = await self.agent.run(prompt)
                return ProxyRunResult(output=result.output)
            except Exception:
                logger.warning("Proxy LLM call failed — falling back to deterministic route", exc_info=True)

        # Deterministic fallback: always route direct with echo
        decision = RouteDecision(
            route="direct",
            reason="phase1a_deterministic_fallback",
            response=AgentResponse(
                message=prompt,
                needs_approval=False,
            ),
            interaction_register=InteractionRegister.status,
            interaction_mode=InteractionMode.default_and_offer,
            context_profile=self.default_context_profile,
        )
        return ProxyRunResult(output=decision)


def _load_proxy_system_prompt() -> str:
    prompt_path = Path(__file__).resolve().parent / "prompts" / "proxy_system.md"
    if prompt_path.exists():
        prompt_text = prompt_path.read_text(encoding="utf-8").strip()
        if prompt_text:
            return prompt_text
    return DEFAULT_PROXY_SYSTEM_PROMPT


def build_proxy_agent(model: str, default_context_profile: str = "conversation") -> ProxyAgent:
    return ProxyAgent(model=model, default_context_profile=default_context_profile)


__all__ = ["ProxyAgent", "ProxyRunResult", "build_proxy_agent"]
