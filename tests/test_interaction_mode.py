from __future__ import annotations

import pytest
from silas.core.interaction_mode import resolve_interaction_mode
from silas.models.agents import AgentResponse, InteractionMode, InteractionRegister, RouteDecision
from silas.models.gates import GateLane, GateResult
from silas.models.personality import AxisProfile


class _Calibrator:
    def __init__(self, metrics: dict[str, object]) -> None:
        self.metrics = metrics

    def get_metrics(self, scope_id: str) -> dict[str, object]:
        del scope_id
        return self.metrics


class _BrokenCalibrator:
    def get_metrics(self, scope_id: str) -> dict[str, object]:
        del scope_id
        raise RuntimeError("boom")


class _Personality:
    def __init__(self, initiative: float) -> None:
        self.initiative = initiative
        self.calls: list[tuple[str, str]] = []

    async def get_effective_axes(self, scope_id: str, context_key: str) -> AxisProfile:
        self.calls.append((scope_id, context_key))
        return AxisProfile(
            warmth=0.4,
            assertiveness=0.4,
            verbosity=0.4,
            formality=0.4,
            humor=0.4,
            initiative=self.initiative,
            certainty=0.4,
        )


class _BrokenPersonality:
    async def get_effective_axes(self, scope_id: str, context_key: str) -> AxisProfile:
        del scope_id, context_key
        raise TypeError("invalid")


def _route(
    *,
    register: InteractionRegister = InteractionRegister.execution,
    mode: InteractionMode = InteractionMode.default_and_offer,
    context_profile: str = "conversation",
) -> RouteDecision:
    return RouteDecision(
        route="direct",
        reason="test",
        response=AgentResponse(message="ok", needs_approval=False),
        interaction_register=register,
        interaction_mode=mode,
        context_profile=context_profile,
    )


@pytest.mark.asyncio
async def test_risk_override_from_gate_block_forces_confirmation_mode() -> None:
    route = _route(mode=InteractionMode.act_and_report)
    gate_results = [
        GateResult(
            gate_name="risk",
            lane=GateLane.policy,
            action="block",
            reason="requires confirmation",
        )
    ]

    resolved = await resolve_interaction_mode(
        route_decision=route,
        scope_id="owner",
        autonomy_calibrator=_Calibrator({"initiative_level": 1.0}),
        gate_results=gate_results,
        personality_engine=None,
    )

    assert resolved == InteractionMode.confirm_only_when_required


@pytest.mark.asyncio
async def test_planner_override_takes_precedence_over_work_item_and_proxy() -> None:
    route = _route(mode=InteractionMode.default_and_offer)

    resolved = await resolve_interaction_mode(
        route_decision=route,
        scope_id="owner",
        autonomy_calibrator=None,
        gate_results=[],
        personality_engine=None,
        planner_override=InteractionMode.confirm_only_when_required,
        work_item_mode=InteractionMode.act_and_report,
    )

    assert resolved == InteractionMode.confirm_only_when_required


@pytest.mark.asyncio
async def test_work_item_mode_takes_precedence_when_planner_override_absent() -> None:
    route = _route(mode=InteractionMode.default_and_offer)

    resolved = await resolve_interaction_mode(
        route_decision=route,
        scope_id="owner",
        autonomy_calibrator=None,
        gate_results=[],
        personality_engine=None,
        work_item_mode=InteractionMode.act_and_report,
    )

    assert resolved == InteractionMode.act_and_report


@pytest.mark.asyncio
async def test_personality_axes_override_calibrator_initiative_and_use_route_profile() -> None:
    route = _route(register=InteractionRegister.execution, context_profile="planning")
    route_without_proxy_mode = route.model_copy(update={"interaction_mode": None})
    personality = _Personality(initiative=0.95)

    resolved = await resolve_interaction_mode(
        route_decision=route_without_proxy_mode,
        scope_id="owner",
        autonomy_calibrator=_Calibrator({"initiative_level": 0.1, "high_initiative_min": 0.7}),
        gate_results=[],
        personality_engine=personality,
    )

    assert resolved == InteractionMode.act_and_report
    assert personality.calls == [("owner", "planning")]


@pytest.mark.asyncio
async def test_calibrator_and_personality_failures_fall_back_to_register_default() -> None:
    route = _route(register=InteractionRegister.review)
    route_without_proxy_mode = route.model_copy(update={"interaction_mode": None})

    resolved = await resolve_interaction_mode(
        route_decision=route_without_proxy_mode,
        scope_id="owner",
        autonomy_calibrator=_BrokenCalibrator(),
        gate_results=[],
        personality_engine=_BrokenPersonality(),
    )

    assert resolved == InteractionMode.default_and_offer


@pytest.mark.asyncio
async def test_calibrator_risk_flag_forces_confirmation_without_gate_results() -> None:
    route = _route(mode=InteractionMode.act_and_report)

    resolved = await resolve_interaction_mode(
        route_decision=route,
        scope_id="owner",
        autonomy_calibrator=_Calibrator({"risk_requires_confirmation": True}),
        gate_results=[],
        personality_engine=None,
    )

    assert resolved == InteractionMode.confirm_only_when_required
