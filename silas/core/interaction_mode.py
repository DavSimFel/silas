"""Interaction-mode resolver.

This module centralizes turn-level mode selection so Stream, planner paths,
and work-item execution all derive behavior from one deterministic policy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real

from silas.models.agents import InteractionMode, InteractionRegister, RouteDecision
from silas.models.gates import GateResult
from silas.personality.engine import SilasPersonalityEngine as PersonalityEngine
from silas.proactivity.calibrator import SimpleAutonomyCalibrator as AutonomyCalibrator

_DEFAULT_HIGH_INITIATIVE_MIN = 0.70
_DEFAULT_MODE_BY_REGISTER: dict[InteractionRegister, InteractionMode] = {
    InteractionRegister.exploration: InteractionMode.default_and_offer,
    InteractionRegister.execution: InteractionMode.confirm_only_when_required,
    InteractionRegister.review: InteractionMode.default_and_offer,
    InteractionRegister.status: InteractionMode.default_and_offer,
}


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, Real):
        return float(value)
    return default


def _gate_risk_requires_confirmation(gate_results: Sequence[GateResult]) -> bool:
    for result in gate_results:
        if result.action in {"block", "require_approval"}:
            return True
        if "require_confirmation" in result.flags:
            return True
    return False


def _resolve_with_precedence(
    *,
    proxy_mode: InteractionMode | None,
    planner_override: InteractionMode | None,
    work_item_mode: InteractionMode | None,
    risk_requires_confirmation: bool,
    initiative_level: float,
    interaction_register: InteractionRegister,
    high_initiative_min: float,
    default_mode_by_register: Mapping[InteractionRegister, InteractionMode],
) -> InteractionMode:
    if risk_requires_confirmation:
        return InteractionMode.confirm_only_when_required
    if planner_override is not None:
        return planner_override
    if work_item_mode is not None:
        return work_item_mode
    if proxy_mode is not None:
        return proxy_mode
    if initiative_level >= high_initiative_min:
        if interaction_register == InteractionRegister.execution:
            return InteractionMode.act_and_report
        if interaction_register == InteractionRegister.exploration:
            return InteractionMode.default_and_offer
    return default_mode_by_register.get(
        interaction_register,
        InteractionMode.default_and_offer,
    )


async def resolve_interaction_mode(
    *,
    route_decision: RouteDecision,
    scope_id: str,
    autonomy_calibrator: AutonomyCalibrator | None,
    gate_results: Sequence[GateResult],
    personality_engine: PersonalityEngine | None,
    planner_override: InteractionMode | None = None,
    work_item_mode: InteractionMode | None = None,
    default_mode_by_register: Mapping[InteractionRegister, InteractionMode] | None = None,
) -> InteractionMode:
    """Resolve a single effective interaction mode for the active turn.

    Why: routing, execution, and approval behavior all depend on interaction
    mode, so mode policy must be derived once from shared turn evidence.
    """
    autonomy_state: Mapping[str, object] = {}
    if autonomy_calibrator is not None:
        get_metrics = getattr(autonomy_calibrator, "get_metrics", None)
        if callable(get_metrics):
            try:
                raw_state = get_metrics(scope_id)
            except (RuntimeError, ValueError, TypeError):
                raw_state = {}
            if isinstance(raw_state, Mapping):
                autonomy_state = raw_state

    high_initiative_min = _coerce_float(
        autonomy_state.get("high_initiative_min"),
        _DEFAULT_HIGH_INITIATIVE_MIN,
    )
    initiative_level = _coerce_float(autonomy_state.get("initiative_level"), 0.0)

    if personality_engine is not None:
        try:
            axes = await personality_engine.get_effective_axes(
                scope_id,
                route_decision.context_profile,
            )
        except (RuntimeError, ValueError, TypeError):
            axes = None
        if axes is not None:
            initiative_level = float(axes.initiative)

    risk_requires_confirmation = bool(autonomy_state.get("risk_requires_confirmation", False))
    if not risk_requires_confirmation:
        risk_requires_confirmation = _gate_risk_requires_confirmation(gate_results)

    register_defaults = default_mode_by_register or _DEFAULT_MODE_BY_REGISTER
    return _resolve_with_precedence(
        proxy_mode=route_decision.interaction_mode,
        planner_override=planner_override,
        work_item_mode=work_item_mode,
        risk_requires_confirmation=risk_requires_confirmation,
        initiative_level=initiative_level,
        interaction_register=route_decision.interaction_register,
        high_initiative_min=high_initiative_min,
        default_mode_by_register=register_defaults,
    )


__all__ = ["resolve_interaction_mode"]
