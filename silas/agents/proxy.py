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
            logger.warning("Failed to initialize PydanticAI Agent for proxy — using fallback mode")
            self.agent = None
            self._llm_available = False

    async def run(self, prompt: str) -> ProxyRunResult:
        # Try LLM path first
        if self.agent is not None and self._llm_available:
            try:
                result = await self.agent.run(prompt)
                return ProxyRunResult(output=result.output)
            except Exception:
                logger.warning("Proxy LLM call failed — falling back to deterministic route")

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
        return prompt_path.read_text(encoding="utf-8")
    return "You are the Silas Proxy agent. Route messages as direct or planner."


def build_proxy_agent(model: str, default_context_profile: str = "conversation") -> ProxyAgent:
    return ProxyAgent(model=model, default_context_profile=default_context_profile)


__all__ = ["ProxyAgent", "ProxyRunResult", "build_proxy_agent"]
