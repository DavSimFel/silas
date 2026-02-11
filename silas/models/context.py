from __future__ import annotations

from datetime import datetime
from enum import Enum
from math import floor

from pydantic import BaseModel, Field, field_validator, model_validator

from silas.models.messages import TaintLevel, utc_now


class ContextZone(str, Enum):
    system = "system"
    chronicle = "chronicle"
    memory = "memory"
    workspace = "workspace"


class ContextProfile(BaseModel):
    name: str
    chronicle_pct: float
    memory_pct: float
    workspace_pct: float

    @field_validator("chronicle_pct", "memory_pct", "workspace_pct")
    @classmethod
    def _validate_pct_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("profile percentages must be in the range [0.0, 1.0]")
        return value

    @model_validator(mode="after")
    def _validate_total_budget(self) -> ContextProfile:
        total = self.chronicle_pct + self.memory_pct + self.workspace_pct
        if total > 0.80:
            raise ValueError("profile percentages must sum to <= 0.80")
        return self


class ContextItem(BaseModel):
    ctx_id: str
    zone: ContextZone
    content: str
    token_count: int
    created_at: datetime = Field(default_factory=utc_now)
    turn_number: int
    source: str
    taint: TaintLevel = TaintLevel.external
    kind: str
    relevance: float = 1.0
    masked: bool = False
    pinned: bool = False

    @field_validator("created_at")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class ContextSubscription(BaseModel):
    sub_id: str
    sub_type: str
    target: str
    zone: ContextZone
    created_at: datetime = Field(default_factory=utc_now)
    turn_created: int
    content_hash: str
    active: bool = True
    token_count: int = 0

    @field_validator("created_at")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class TokenBudget(BaseModel):
    total: int = 180_000
    system_max: int = 8_000
    skill_metadata_budget_pct: float = 0.02
    eviction_threshold_pct: float = 0.80
    scorer_threshold_pct: float = 0.90
    max_subscription_tokens: int = 2_000
    subscription_ttl_turns: int = 10
    observation_mask_after_turns: int = 5
    profiles: dict[str, ContextProfile] = Field(default_factory=dict)
    default_profile: str = "conversation"

    @field_validator("skill_metadata_budget_pct")
    @classmethod
    def _validate_skill_budget_pct(cls, value: float) -> float:
        if not 0.0 <= value <= 0.10:
            raise ValueError("skill_metadata_budget_pct must be in the range [0.0, 0.10]")
        return value

    @model_validator(mode="after")
    def _validate_default_profile(self) -> TokenBudget:
        if self.profiles and self.default_profile not in self.profiles:
            raise ValueError("default_profile must exist in profiles")
        return self

    def allocable_budget(self, system_zone_tokens: int) -> int:
        system_actual = min(system_zone_tokens, self.system_max)
        return max(self.total - system_actual, 0)

    def zone_budget(self, zone: ContextZone, profile: ContextProfile, system_zone_tokens: int) -> int:
        allocable = self.allocable_budget(system_zone_tokens)
        if zone == ContextZone.chronicle:
            return floor(allocable * profile.chronicle_pct)
        if zone == ContextZone.memory:
            return floor(allocable * profile.memory_pct)
        if zone == ContextZone.workspace:
            return floor(allocable * profile.workspace_pct)
        return self.system_max


__all__ = [
    "ContextZone",
    "ContextProfile",
    "ContextItem",
    "ContextSubscription",
    "TokenBudget",
]
