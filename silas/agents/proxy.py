"""Proxy Agent — routes messages as direct or planner.

Phase 1a: Attempts LLM call via PydanticAI Agent. Falls back to
deterministic direct routing if the LLM call fails or no API key
is configured.

WI-2 enhancement: optional tool loop. When use_tools=True and tools
are provided, the proxy can call tools (memory_search, web_search, etc.)
before producing its final RouteDecision. Backward compatible — defaults
to the original one-shot behavior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import Agent

from silas.models.agents import AgentResponse, InteractionMode, InteractionRegister, RouteDecision

if TYPE_CHECKING:
    from silas.tools.common import AgentDeps
    from silas.tools.toolsets import AgentToolBundle

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

When tools are available, use them to gather information BEFORE making your
routing decision. For example, search memory for relevant context or look up
facts via web search.
"""


@dataclass(slots=True)
class ProxyRunResult:
    output: RouteDecision


class ProxyAgent:
    """Proxy agent wrapper with optional tool loop.

    Wraps a PydanticAI Agent with RouteDecision output type.
    When use_tools=True and tools are provided, the agent can call
    tools during its run before producing the final RouteDecision.

    Why a feature flag: incremental rollout per spec §9 migration plan.
    Existing callers that don't pass tools get identical behavior.
    """

    def __init__(
        self,
        model: str,
        default_context_profile: str = "conversation",
        *,
        use_tools: bool = False,
        tool_bundle: AgentToolBundle | None = None,
    ) -> None:
        self.model = model
        self.default_context_profile = default_context_profile
        self.system_prompt = _load_proxy_system_prompt()
        self._llm_available = True
        # Why store bundle separately: we pass console toolset and custom
        # tools to the Agent only when use_tools is True.
        self._use_tools = use_tools and tool_bundle is not None
        self._tool_bundle = tool_bundle

        try:
            self.agent = Agent(
                model=model,
                output_type=RouteDecision,
                system_prompt=self.system_prompt,
                tools=tool_bundle.custom_tools if self._use_tools and tool_bundle else [],
                toolsets=[tool_bundle.console_toolset] if self._use_tools and tool_bundle else [],
            )
        except (ImportError, ValueError, TypeError, RuntimeError) as exc:
            logger.warning("Failed to initialize PydanticAI Agent for proxy — using fallback mode: %s", exc)
            self.agent = None
            self._llm_available = False

    async def run(self, prompt: str, deps: AgentDeps | None = None) -> ProxyRunResult:
        """Run the proxy agent, optionally with tool-loop deps.

        When deps is provided and tools are registered, the agent can use
        tools during its run. When deps is None, falls back to one-shot.
        """
        # Try LLM path first
        if self.agent is not None and self._llm_available:
            try:
                if self._use_tools and deps is not None:
                    result = await self.agent.run(prompt, deps=deps)
                else:
                    result = await self.agent.run(prompt)
                return ProxyRunResult(output=result.output)
            except (ConnectionError, TimeoutError, ValueError, RuntimeError):
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


def build_proxy_agent(
    model: str,
    default_context_profile: str = "conversation",
    *,
    use_tools: bool = False,
    tool_bundle: AgentToolBundle | None = None,
) -> ProxyAgent:
    """Factory for ProxyAgent with optional tool loop support."""
    return ProxyAgent(
        model=model,
        default_context_profile=default_context_profile,
        use_tools=use_tools,
        tool_bundle=tool_bundle,
    )


__all__ = ["ProxyAgent", "ProxyRunResult", "build_proxy_agent"]
