from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator, model_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Suggestion(BaseModel):
    id: str
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: str
    category: str
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("created_at")
    @classmethod
    def _ensure_created_at_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class SuggestionProposal(Suggestion):
    action_hint: str
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def _ensure_expires_at_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("expires_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_expiry(self) -> SuggestionProposal:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be greater than created_at")
        return self


__all__ = ["Suggestion", "SuggestionProposal"]
