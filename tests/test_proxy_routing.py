from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from silas.agents.proxy import ProxyAgent
from silas.config import SilasSettings
from silas.main import build_stream
from silas.models.agents import (
    AgentResponse,
    InteractionMode,
    InteractionRegister,
    RouteDecision,
)
from silas.models.gates import GateTrigger
from silas.tools.resolver import LiveSkillResolver

from tests.fakes import TestModel


@pytest.mark.asyncio
async def test_proxy_returns_direct_route_for_simple_message(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingAgent:
        def __init__(self, **_: object) -> None:
            raise RuntimeError("llm unavailable")

    monkeypatch.setattr("silas.agents.proxy.Agent", FailingAgent)
    proxy = ProxyAgent(model="test-model", default_context_profile="conversation")

    result = await proxy.run("hello")

    assert result.output.route == "direct"
    assert result.output.response is not None
    assert result.output.response.message == "hello"
    assert result.output.context_profile == "conversation"


def test_build_stream_configures_route_profiles_from_settings(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SilasSettings.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "context": {
                "default_profile": "conversation",
                "profiles": {
                    "conversation": {"chronicle_pct": 0.45, "memory_pct": 0.20, "workspace_pct": 0.15},
                    "planning": {"chronicle_pct": 0.15, "memory_pct": 0.25, "workspace_pct": 0.35},
                    "custom_profile": {
                        "chronicle_pct": 0.30,
                        "memory_pct": 0.20,
                        "workspace_pct": 0.25,
                    },
                },
            },
        }
    )

    monkeypatch.setattr("silas.main.build_proxy_agent", lambda model, default_context_profile: TestModel())
    RouteDecision.configure_profiles({"conversation", "coding", "research", "support", "planning"})

    build_stream(settings)

    decision = RouteDecision(
        route="direct",
        reason="profile config check",
        response=AgentResponse(message="ok"),
        interaction_register=InteractionRegister.status,
        interaction_mode=InteractionMode.default_and_offer,
        context_profile="planning",
    )
    assert decision.context_profile == "planning"


def test_build_stream_wires_output_gate_runner(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SilasSettings.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "output_gates": [
                {
                    "name": "taint_guard",
                    "on": GateTrigger.every_agent_response.value,
                    "check": "taint_ceiling",
                    "config": {"threshold": "external"},
                }
            ],
        }
    )
    monkeypatch.setattr("silas.main.build_proxy_agent", lambda model, default_context_profile: TestModel())

    stream, _ = build_stream(settings)

    assert stream.output_gate_runner is not None


def test_build_stream_injects_signing_key_into_stream(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SilasSettings.model_validate({"data_dir": str(tmp_path / "data")})
    signing_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr("silas.main.build_proxy_agent", lambda model, default_context_profile: TestModel())

    stream, _ = build_stream(settings, signing_key=signing_key)

    assert stream._signing_key is signing_key
    assert stream._nonce_store is not None


def test_build_stream_wires_skill_loader_and_live_resolver(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SilasSettings.model_validate({"data_dir": str(tmp_path / "data")})
    monkeypatch.setattr("silas.main.build_proxy_agent", lambda model, default_context_profile: TestModel())

    stream, _ = build_stream(settings)

    assert stream.turn_context.skill_loader is not None
    assert isinstance(stream.turn_context.skill_resolver, LiveSkillResolver)
