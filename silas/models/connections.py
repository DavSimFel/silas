from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

type AuthStrategy = Literal["device_code", "browser_redirect", "secure_input"]


class SecureInputRequest(BaseModel):
    ref_id: str
    label: str
    input_hint: str | None = None
    guidance: dict[str, object] = Field(default_factory=dict)


class SecureInputCompleted(BaseModel):
    ref_id: str
    success: bool


class SetupStep(BaseModel):
    type: Literal[
        "device_code",
        "browser_redirect",
        "secure_input",
        "progress",
        "completion",
        "failure",
    ]
    verification_url: str | None = None
    user_code: str | None = None
    expires_in: int | None = None
    poll_interval: int | None = None
    auth_url: str | None = None
    listening_on: str | None = None
    request: SecureInputRequest | None = None
    message: str | None = None
    progress_pct: float | None = None
    success: bool | None = None
    summary: str | None = None
    permissions_granted: list[str] = Field(default_factory=list)
    failure: ConnectionFailure | None = None


class SetupStepResponse(BaseModel):
    step_type: str
    action: str


class HealthCheckResult(BaseModel):
    healthy: bool
    token_expires_at: datetime | None = None
    refresh_token_expires_at: datetime | None = None
    latency_ms: int = 0
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class RecoveryOption(BaseModel):
    action: str
    label: str
    description: str
    risk_level: Literal["low", "medium", "high"] = "low"


class ConnectionFailure(BaseModel):
    failure_type: str
    service: str
    message: str
    recovery_options: list[RecoveryOption] = Field(default_factory=list)


class Connection(BaseModel):
    connection_id: str
    skill_name: str
    provider: str
    status: Literal["active", "inactive", "error"] = "active"
    permissions_granted: list[str] = Field(default_factory=list)
    token_expires_at: datetime | None = None
    last_refresh: datetime | None = None
    last_health_check: datetime | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "AuthStrategy",
    "Connection",
    "ConnectionFailure",
    "HealthCheckResult",
    "RecoveryOption",
    "SecureInputCompleted",
    "SecureInputRequest",
    "SetupStep",
    "SetupStepResponse",
]
