from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from silas.models.messages import TaintLevel, utc_now


class MemoryType(str, Enum):
    episode = "episode"
    fact = "fact"
    preference = "preference"
    skill = "skill"
    entity = "entity"
    profile = "profile"


class ReingestionTier(str, Enum):
    active = "active"
    low_reingestion = "low_reingestion"
    core = "core"
    dormant = "dormant"


class TrustLevel(str, Enum):
    working = "working"
    verified = "verified"
    constitutional = "constitutional"


class MemoryItem(BaseModel):
    memory_id: str
    content: str
    memory_type: MemoryType
    reingestion_tier: ReingestionTier = ReingestionTier.active
    trust_level: TrustLevel = TrustLevel.working
    taint: TaintLevel = TaintLevel.owner
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    access_count: int = 0
    last_accessed: datetime | None = None
    semantic_tags: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    causal_refs: list[str] = Field(default_factory=list)
    temporal_next: str | None = None
    temporal_prev: str | None = None
    session_id: str | None = None
    embedding: list[float] | None = None
    source_kind: str

    @field_validator("created_at", "updated_at", "valid_from", "valid_until", "last_accessed")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("datetime fields must be timezone-aware")
        return value


__all__ = [
    "MemoryType",
    "ReingestionTier",
    "TrustLevel",
    "MemoryItem",
]
