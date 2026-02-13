from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


class AxisProfile(BaseModel):
    warmth: float
    assertiveness: float
    verbosity: float
    formality: float
    humor: float
    initiative: float
    certainty: float

    @field_validator(
        "warmth",
        "assertiveness",
        "verbosity",
        "formality",
        "humor",
        "initiative",
        "certainty",
    )
    @classmethod
    def _clamp_axis(cls, value: float) -> float:
        return _clamp01(value)


class MoodState(BaseModel):
    energy: float
    patience: float
    curiosity: float
    frustration: float

    @field_validator("energy", "patience", "curiosity", "frustration")
    @classmethod
    def _clamp_mood(cls, value: float) -> float:
        return _clamp01(value)


class VoiceConfig(BaseModel):
    tone: str
    quirks: list[str] = Field(default_factory=list)
    speech_patterns: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)


class PersonaPreset(BaseModel):
    name: str
    axes: AxisProfile
    voice: VoiceConfig


class PersonaState(BaseModel):
    scope_id: str
    baseline_axes: AxisProfile
    mood: MoodState
    active_preset: str = "default"
    voice: VoiceConfig
    last_context: str = ""
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _ensure_updated_at_timezone_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("updated_at must be timezone-aware")
        return value.astimezone(UTC)


class PersonaEvent(BaseModel):
    event_id: str
    scope_id: str
    event_type: str
    trusted: bool
    delta_axes: dict[str, float] = Field(default_factory=dict)
    delta_mood: dict[str, float] = Field(default_factory=dict)
    source: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _ensure_created_at_timezone_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(UTC)


__all__ = [
    "AxisProfile",
    "MoodState",
    "PersonaEvent",
    "PersonaPreset",
    "PersonaState",
    "VoiceConfig",
]
