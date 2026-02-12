from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator, model_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


class UndoEntry(BaseModel):
    entry_id: str
    scope_id: str
    execution_id: str
    reverse_actions: list[dict[str, object]] = Field(default_factory=list)
    summary: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime
    undone_at: datetime | None = None

    @field_validator("created_at", "expires_at", "undone_at")
    @classmethod
    def _ensure_timezone_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("datetime fields must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_temporal_order(self) -> UndoEntry:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be greater than created_at")
        if self.undone_at is not None and self.undone_at < self.created_at:
            raise ValueError("undone_at must be greater than or equal to created_at")
        return self

    def can_undo(self, now: datetime | None = None) -> bool:
        if self.undone_at is not None:
            return False
        now_utc = now if now is not None else datetime.now(UTC)
        return now_utc <= self.expires_at


__all__ = ["UndoEntry"]
