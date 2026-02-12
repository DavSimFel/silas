from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from silas.models.messages import TaintLevel, utc_now


class SandboxConfig(BaseModel):
    backend: str | None = None
    work_dir: str = "./data/sandbox/work"
    network_access: bool = False
    filesystem_read: list[str] = Field(default_factory=list)
    filesystem_write: list[str] = Field(default_factory=list)
    max_memory_mb: int = 512
    max_cpu_seconds: int = 60
    env: dict[str, str] = Field(default_factory=dict)


class Sandbox(BaseModel):
    sandbox_id: str
    config: SandboxConfig
    work_dir: str
    created_at: datetime = Field(default_factory=utc_now)


class ExecutionEnvelope(BaseModel):
    execution_id: str
    step_index: int
    task_description: str
    action: str
    args: dict[str, object] = Field(default_factory=dict)
    input_artifacts: dict[str, str] = Field(default_factory=dict)
    credential_refs: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = 300
    max_output_bytes: int = 100_000
    sandbox_config: SandboxConfig = Field(default_factory=SandboxConfig)


class ExecutionResult(BaseModel):
    execution_id: str
    step_index: int
    success: bool
    return_value: str = ""
    content: list[dict[str, object]] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    taint: TaintLevel = TaintLevel.external
    error: str | None = None
    duration_seconds: float = 0.0
    tokens_used: int = 0
    cost_usd: float = 0.0


ExecutorToolCallStatus = Literal[
    "pending",
    "ok",
    "error",
    "not_found",
    "filtered",
    "approval_required",
    "denied",
]


class ExecutorToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    status: ExecutorToolCallStatus = "pending"
    result: object | None = None
    error: str | None = None


class ExecutorAgentOutput(BaseModel):
    summary: str
    tool_calls: list[ExecutorToolCall] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    last_error: str | None = None


class VerificationResult(BaseModel):
    name: str
    passed: bool
    reason: str
    output: str = ""
    exit_code: int | None = None


class VerificationReport(BaseModel):
    all_passed: bool
    results: list[VerificationResult] = Field(default_factory=list)
    failed: list[VerificationResult] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)


__all__ = [
    "SandboxConfig",
    "Sandbox",
    "ExecutionEnvelope",
    "ExecutionResult",
    "ExecutorToolCallStatus",
    "ExecutorToolCall",
    "ExecutorAgentOutput",
    "VerificationResult",
    "VerificationReport",
]
