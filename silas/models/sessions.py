from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from silas.models.messages import utc_now


class SessionType(StrEnum):
    stream = "stream"
    side = "side"


class Session(BaseModel):
    session_id: str
    session_type: SessionType
    title: str
    created_at: datetime = Field(default_factory=utc_now)
    last_active: datetime = Field(default_factory=utc_now)
    turn_count: int = 0
    active: bool = True
    pinned_ctx_ids: list[str] = Field(default_factory=list)

    @field_validator("created_at", "last_active")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("datetime fields must be timezone-aware")
        return value


__all__ = ["Session", "SessionType"]
