"""Typed queue message contracts for the Silas runtime bus.

Defines the canonical message envelope and payload types that flow between
agents (proxy, planner, executor) and the runtime. These mirror the contracts
in specs/agent-loop-architecture.md §2.1, adapted as Pydantic models for
validation and serialization.

Why Pydantic instead of dataclasses: the rest of Silas uses Pydantic for
serialization (work items, context items), so we stay consistent. Pydantic
also gives us automatic JSON round-tripping for SQLite storage.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

# Why Literal over Enum for ErrorCode/MessageKind: these are wire-format
# strings that appear in JSON payloads and SQLite columns. Literal keeps
# them as plain strings without .value gymnastics.

ErrorCode = Literal[
    "tool_failure",
    "budget_exceeded",
    "gate_blocked",
    "approval_denied",
    "verification_failed",
    "timeout",
]

MessageKind = Literal[
    "plan_request",
    "plan_result",
    "execution_request",
    "execution_status",
    "research_request",
    "research_result",
    "planner_guidance",
    "replan_request",
    "approval_request",
    "approval_result",
    "user_message",
    "agent_response",
    "system_event",
]

Sender = Literal["user", "proxy", "planner", "executor", "runtime"]


class ExecutionStatus(enum.StrEnum):
    """Possible states for a work item execution.

    Maps to the execution_status payload in §2.1. Uses str mixin so
    enum values serialize as plain strings in JSON/SQLite.
    """

    running = "running"
    done = "done"
    failed = "failed"
    stuck = "stuck"
    blocked = "blocked"
    verification_failed = "verification_failed"


class ErrorPayload(BaseModel):
    """Structured error information attached to failure messages.

    Carried as QueueMessage.payload when an agent or runtime needs to
    communicate a typed error with retry guidance.
    """

    error_code: ErrorCode
    message: str
    origin_agent: Sender
    retryable: bool
    detail: str | None = None


class StatusPayload(BaseModel):
    """Execution status update for a work item.

    Carried as QueueMessage.payload for message_kind='execution_status'.
    The spec (§2.1) requires status messages to include attempt info.
    """

    status: ExecutionStatus
    work_item_id: str
    attempt: int
    detail: str | None = None


# Why a Union alias: consumers can narrow on the payload type to determine
# whether they're handling an error vs. a status update, without inspecting
# message_kind separately.
QueuePayload = ErrorPayload | StatusPayload


def _generate_uuid() -> str:
    """Generate a new UUID4 string for message IDs."""
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(UTC)


class QueueMessage(BaseModel):
    """Canonical message envelope for the durable queue bus.

    Every inter-agent communication flows through this type. The runtime
    serializes it to JSON for SQLite persistence, and deserializes on lease.

    Design rationale:
    - `id` is auto-generated to guarantee uniqueness (idempotency key).
    - `trace_id` propagates unchanged across all hops for distributed tracing.
    - `payload` is dict[str, object] rather than QueuePayload because the
      store serializes to JSON; consumers cast to the appropriate typed
      payload based on message_kind.
    - `lease_id` and `lease_expires_at` are queue infrastructure fields,
      set by the store during lease operations, not by producers.
    """

    id: str = Field(default_factory=_generate_uuid)
    queue_name: str = ""
    message_kind: MessageKind
    sender: Sender
    trace_id: str = Field(default_factory=_generate_uuid)
    # Why dict[str, object] instead of Any: object is the most permissive
    # type that still communicates "structured data", and avoids a blanket
    # Any that would suppress all type checking on consumers.
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    lease_id: str | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0


__all__ = [
    "ErrorCode",
    "ErrorPayload",
    "ExecutionStatus",
    "MessageKind",
    "QueueMessage",
    "QueuePayload",
    "Sender",
    "StatusPayload",
]
