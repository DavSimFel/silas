from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    from pydantic_ai import Agent
except ModuleNotFoundError:  # pragma: no cover - fallback for constrained environments
    class Agent:  # type: ignore[override]
        def __init__(self, model: str, output_type: type[object], system_prompt: str) -> None:
            self.model = model
            self.output_type = output_type
            self.system_prompt = system_prompt

from silas.models.agents import AgentResponse, InteractionMode, InteractionRegister, RouteDecision


@dataclass(slots=True)
class ProxyRunResult:
    output: RouteDecision


class ProxyAgent:
    """Phase 1a proxy wrapper.

    The underlying PydanticAI agent is created with `RouteDecision` output type,
    while runtime behavior remains deterministic (always `route="direct"`).
    """

    def __init__(self, model: str, default_context_profile: str = "conversation") -> None:
        self.model = model
        self.default_context_profile = default_context_profile
        self.system_prompt = _load_proxy_system_prompt()
        self.agent = Agent(
            model=model,
            output_type=RouteDecision,
            system_prompt=self.system_prompt,
        )

    async def run(self, prompt: str) -> ProxyRunResult:
        decision = RouteDecision(
            route="direct",
            reason="phase1a_direct_route",
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
    return prompt_path.read_text(encoding="utf-8")


def build_proxy_agent(model: str, default_context_profile: str = "conversation") -> ProxyAgent:
    return ProxyAgent(model=model, default_context_profile=default_context_profile)


__all__ = ["ProxyAgent", "ProxyRunResult", "build_proxy_agent"]
