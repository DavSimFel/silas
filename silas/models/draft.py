from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DraftVerdict(str, Enum):
    approve = "approve"
    edit = "edit"
    rephrase = "rephrase"
    reject = "reject"


class DraftReview(BaseModel):
    review_id: str
    context: str
    draft: str
    metadata: dict[str, object] = Field(default_factory=dict)
    verdict: DraftVerdict | None = None
    edited_text: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    decided_at: datetime | None = None

    @field_validator("created_at", "decided_at")
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
    def _validate_edit_requirements(self) -> DraftReview:
        requires_edit_text = self.verdict in {DraftVerdict.edit, DraftVerdict.rephrase}
        if requires_edit_text and not (self.edited_text and self.edited_text.strip()):
            raise ValueError("edited_text is required for edit/rephrase verdicts")
        if self.verdict is None and self.decided_at is not None:
            raise ValueError("decided_at requires a verdict")
        if self.decided_at is not None and self.decided_at < self.created_at:
            raise ValueError("decided_at must be greater than or equal to created_at")
        return self


__all__ = ["DraftVerdict", "DraftReview"]
