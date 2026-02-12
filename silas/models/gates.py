from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from silas.models.expectation import Expectation


class GateType(StrEnum):
    numeric_range = "numeric_range"
    string_match = "string_match"
    regex = "regex"
    file_valid = "file_valid"
    approval_always = "approval_always"
    custom_check = "custom_check"


class GateLane(StrEnum):
    policy = "policy"
    quality = "quality"


class GateProvider(StrEnum):
    guardrails_ai = "guardrails_ai"
    predicate = "predicate"
    llm = "llm"
    script = "script"
    custom = "custom"


class GateTrigger(StrEnum):
    every_user_message = "every_user_message"
    every_agent_response = "every_agent_response"
    after_step = "after_step"
    on_tool_call = "on_tool_call"


class Gate(BaseModel):
    name: str
    on: GateTrigger
    after_step: int | None = None
    provider: GateProvider = GateProvider.predicate
    type: GateType = GateType.string_match
    check: str | None = None
    config: dict[str, object] = Field(default_factory=dict)
    extract: str | None = None
    auto_approve: dict[str, float] | None = None
    require_approval: dict[str, float] | None = None
    block: dict[str, list[float]] | None = None
    allowed_values: list[str] | None = None
    approval_values: list[str] | None = None
    on_block: str = "report"
    check_command: str | None = None
    check_expect: Expectation | None = None
    promote_to_policy: bool = False

    @property
    def lane(self) -> GateLane:
        if self.provider == GateProvider.llm and not self.promote_to_policy:
            return GateLane.quality
        return GateLane.policy

    @model_validator(mode="after")
    def _validate_after_step_trigger(self) -> Gate:
        if self.on == GateTrigger.after_step and self.after_step is None:
            raise ValueError("after_step is required when on='after_step'")
        if self.on != GateTrigger.after_step and self.after_step is not None:
            raise ValueError("after_step must be None unless on='after_step'")
        return self


class AccessLevel(BaseModel):
    description: str
    tools: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    expires_after: int | None = None


class GateResult(BaseModel):
    gate_name: str
    lane: GateLane
    action: Literal["continue", "block", "require_approval"]
    reason: str
    value: str | float | None = None
    score: float | None = None
    flags: list[str] = Field(default_factory=list)
    modified_context: dict[str, object] | None = None

    @model_validator(mode="after")
    def _validate_lane_semantics(self) -> GateResult:
        if self.lane == GateLane.quality and self.action != "continue":
            raise ValueError("quality-lane gates may only return action='continue'")
        return self


ALLOWED_MUTATIONS = frozenset({"response", "message", "tool_args"})


__all__ = [
    "ALLOWED_MUTATIONS",
    "AccessLevel",
    "Gate",
    "GateLane",
    "GateProvider",
    "GateResult",
    "GateTrigger",
    "GateType",
]
