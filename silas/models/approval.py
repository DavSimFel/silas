from __future__ import annotations

import base64
from binascii import Error as BinasciiError
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    PlainSerializer,
    PlainValidator,
    field_validator,
    model_validator,
)

from silas.models.messages import utc_now


def _validate_base64_bytes(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        try:
            return base64.b64decode(value.encode("utf-8"), validate=True)
        except (
            BinasciiError
        ) as exc:  # pragma: no cover - exact decoder errors are implementation detail
            raise ValueError("invalid base64 data") from exc
    raise TypeError("Base64Bytes must be provided as bytes or base64 string")


def _serialize_base64_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("utf-8")


Base64Bytes = Annotated[
    bytes,
    PlainValidator(_validate_base64_bytes),
    PlainSerializer(_serialize_base64_bytes, return_type=str, when_used="json"),
]


class ApprovalScope(StrEnum):
    full_plan = "full_plan"
    single_step = "single_step"
    step_range = "step_range"
    tool_type = "tool_type"
    skill_install = "skill_install"
    credential_use = "credential_use"
    budget = "budget"
    self_update = "self_update"
    connection_act = "connection_act"
    connection_manage = "connection_manage"
    autonomy_threshold = "autonomy_threshold"
    standing = "standing"


class ApprovalVerdict(StrEnum):
    approved = "approved"
    declined = "declined"
    edit_requested = "edit_requested"
    conditional = "conditional"


class ApprovalDecision(BaseModel):
    verdict: ApprovalVerdict
    approval_strength: Literal["tap"] = "tap"
    conditions: dict[str, object] = Field(default_factory=dict)


class ApprovalToken(BaseModel):
    token_id: str
    plan_hash: str
    work_item_id: str
    scope: ApprovalScope
    verdict: ApprovalVerdict
    signature: Base64Bytes
    issued_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    nonce: str
    approval_strength: Literal["tap"] = "tap"
    conditions: dict[str, object] = Field(default_factory=dict)
    executions_used: int = 0
    max_executions: int = 1
    execution_nonces: list[str] = Field(default_factory=list)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("datetime fields must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_standing_scope(self) -> ApprovalToken:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be greater than issued_at")
        if self.scope == ApprovalScope.standing and "spawn_policy_hash" not in self.conditions:
            raise ValueError("standing approvals require conditions.spawn_policy_hash")
        return self


class PendingApproval(BaseModel):
    token: ApprovalToken
    requested_at: datetime = Field(default_factory=utc_now)
    decision: ApprovalDecision | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None

    @field_validator("requested_at", "resolved_at")
    @classmethod
    def _ensure_timezone_aware_optional(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("datetime fields must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_resolution_fields(self) -> PendingApproval:
        if self.decision is None:
            if self.resolved_at is not None or self.resolved_by is not None:
                raise ValueError("resolved fields require a decision")
            return self

        if self.resolved_at is None or self.resolved_by is None:
            raise ValueError("resolved_at and resolved_by are required when decision is set")
        return self


__all__ = [
    "ApprovalDecision",
    "ApprovalScope",
    "ApprovalToken",
    "ApprovalVerdict",
    "Base64Bytes",
    "PendingApproval",
]
