"""Goal-related supporting models.

Goal functionality lives in the Topic model (silas/topics/model.py).
This module retains the reusable schedule and approval types that are
referenced by the approval system and tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from silas.models.approval import ApprovalToken


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Schedule(BaseModel):
    """Schedule specification for a Topic (cron, interval, or one-shot)."""

    kind: Literal["cron", "interval", "once"]
    cron_expr: str | None = None
    interval_seconds: int | None = None
    run_at: datetime | None = None

    @field_validator("run_at")
    @classmethod
    def _ensure_run_at_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("run_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_schedule_shape(self) -> Schedule:
        if self.kind == "cron":
            if not self.cron_expr:
                raise ValueError("cron schedules require cron_expr")
            if self.interval_seconds is not None or self.run_at is not None:
                raise ValueError("cron schedules only allow cron_expr")
        elif self.kind == "interval":
            if self.interval_seconds is None or self.interval_seconds <= 0:
                raise ValueError("interval schedules require interval_seconds > 0")
            if self.cron_expr is not None or self.run_at is not None:
                raise ValueError("interval schedules only allow interval_seconds")
        elif self.kind == "once":
            if self.run_at is None:
                raise ValueError("once schedules require run_at")
            if self.cron_expr is not None or self.interval_seconds is not None:
                raise ValueError("once schedules only allow run_at")
        return self


# Backward-compat alias — keep imports that used GoalSchedule working.
GoalSchedule = Schedule


class StandingApproval(BaseModel):
    approval_id: str
    goal_id: str
    policy_hash: str
    granted_by: str
    granted_at: datetime
    expires_at: datetime | None = None
    max_uses: int | None = None
    uses_remaining: int | None = None
    approval_token: ApprovalToken | None = None

    @field_validator("granted_at", "expires_at")
    @classmethod
    def _ensure_timezone_aware_optional(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("datetime fields must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_limits(self) -> StandingApproval:
        if self.expires_at is not None and self.expires_at <= self.granted_at:
            raise ValueError("expires_at must be greater than granted_at")

        if self.max_uses is not None and self.max_uses < 1:
            raise ValueError("max_uses must be >= 1")

        if self.uses_remaining is None:
            if self.max_uses is not None:
                self.uses_remaining = self.max_uses
            return self

        if self.uses_remaining < 0:
            raise ValueError("uses_remaining must be >= 0")

        if self.max_uses is not None and self.uses_remaining > self.max_uses:
            raise ValueError("uses_remaining cannot exceed max_uses")
        return self


__all__ = ["GoalSchedule", "Schedule", "StandingApproval"]
