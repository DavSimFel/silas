from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PreferenceSignal(BaseModel):
    signal_id: str
    scope_id: str
    signal_type: Literal["correction", "praise", "edit", "override", "style_feedback"]
    context: str
    original_value: str | None = None
    corrected_value: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("created_at")
    @classmethod
    def _ensure_created_at_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class InferredPreference(BaseModel):
    preference_id: str
    scope_id: str
    category: str
    description: str
    confidence: float
    supporting_signals: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @field_validator("created_at", "updated_at")
    @classmethod
    def _ensure_datetime_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("datetime fields must be timezone-aware")
        return value


__all__ = ["InferredPreference", "PreferenceSignal"]
