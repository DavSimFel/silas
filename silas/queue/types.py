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

from silas.models.messages import TaintLevel as TaintLevel

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

# Why a dedicated Literal for urgency: the spec (§2.1) defines exactly three
# levels. A Literal constrains wire values without enum overhead.
Urgency = Literal["background", "informational", "needs_attention"]


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


# ── Typed Payload Models ─────────────────────────────────────────────
# Why typed payloads: the spec requires structured contracts per message_kind.
# Raw dict[str, object] loses type safety and forces consumers to do manual
# key lookups with str casts. These models make the contract explicit.


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


class UserMessagePayload(BaseModel):
    """Payload for message_kind='user_message'."""

    text: str
    metadata: dict[str, object] | None = None


class PlanRequestPayload(BaseModel):
    """Payload for message_kind='plan_request'."""

    user_request: str
    reason: str = ""
    # Why optional goal fields: autonomous goals from the scheduler bypass
    # proxy and go directly to planner, carrying goal_id.
    goal_id: str | None = None
    autonomous: bool = False


class ExecutionRequestPayload(BaseModel):
    """Payload for message_kind='execution_request'."""

    work_item_id: str
    task_description: str = ""
    body: str = ""


class AgentResponsePayload(BaseModel):
    """Payload for message_kind='agent_response'."""

    text: str
    message: str = ""


class ResearchConstraints(BaseModel):
    """Planner tells executor exactly what format to return.

    Per §2.1: runtime MUST clamp tools_allowed to RESEARCH_TOOL_ALLOWLIST.
    """

    return_format: str
    max_tokens: int = 500
    tools_allowed: list[str] = Field(
        default_factory=lambda: ["web_search", "read_file", "memory_search"]
    )


# Why a Union alias: consumers can narrow on the payload type to determine
# whether they're handling an error vs. a status update, without inspecting
# message_kind separately.
QueuePayload = (
    ErrorPayload
    | StatusPayload
    | UserMessagePayload
    | PlanRequestPayload
    | ExecutionRequestPayload
    | AgentResponsePayload
)


def _generate_uuid() -> str:
    """Generate a new UUID4 string for message IDs."""
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(UTC)


# ── Payload Parsing Helpers ──────────────────────────────────────────
# Why helpers instead of a discriminated union: the payload dict is the
# wire format (JSON in SQLite). Consumers call these to get typed access
# without changing the serialization format or breaking backward compat.

_KIND_TO_PAYLOAD: dict[str, type[BaseModel]] = {
    "user_message": UserMessagePayload,
    "plan_request": PlanRequestPayload,
    "execution_request": ExecutionRequestPayload,
    "agent_response": AgentResponsePayload,
    "execution_status": StatusPayload,
}


def parse_payload(message_kind: str, payload: dict[str, object]) -> BaseModel | None:
    """Parse a raw payload dict into the appropriate typed model.

    Returns None if the message_kind has no registered typed payload
    or if the payload doesn't validate (backward compat for old messages).
    """
    model_cls = _KIND_TO_PAYLOAD.get(message_kind)
    if model_cls is None:
        return None
    try:
        return model_cls.model_validate(payload)
    except Exception:
        # Why swallow: old messages with partial fields should not crash
        # consumers. Callers fall back to raw dict access.
        return None


class QueueMessage(BaseModel):
    """Canonical message envelope for the durable queue bus.

    Every inter-agent communication flows through this type. The runtime
    serializes it to JSON for SQLite persistence, and deserializes on lease.

    Design rationale:
    - `id` is auto-generated to guarantee uniqueness (idempotency key).
    - `trace_id` propagates unchanged across all hops for distributed tracing.
    - `payload` is dict[str, object] for extensibility; typed fields below
      promote spec-mandated metadata to first-class attrs.
    - `lease_id` and `lease_expires_at` are queue infrastructure fields,
      set by the store during lease operations, not by producers.

    New in this revision (§2.1 alignment):
    - scope_id, taint, task_id, parent_task_id, work_item_id, approval_token,
      urgency are now first-class fields instead of buried in payload dicts.
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

    # ── Spec §2.1 first-class fields ────────────────────────────────
    # Why promoted from payload: these are cross-cutting concerns that
    # multiple consumers inspect. Typed fields catch misuse at construction
    # time rather than at runtime dict-key lookup.

    # Executor scope tracking — isolates worktrees/artifacts per connection
    scope_id: str | None = None
    # Security taint propagated from inbound message source
    taint: TaintLevel | None = None
    # Links related messages across the plan→execute→status chain
    task_id: str | None = None
    # Enables sub-task hierarchy (research sub-tasks under a parent)
    parent_task_id: str | None = None
    # Reference to the work item being executed
    work_item_id: str | None = None
    # Authorization token consumed by the approval engine at execution entry
    approval_token: str | None = None
    # Priority hint for consumer scheduling decisions
    urgency: Urgency = "informational"

    def typed_payload(self) -> BaseModel | None:
        """Parse payload dict into the appropriate typed model for this message_kind.

        Returns None if no typed model exists or validation fails.
        Consumers should prefer this over raw payload dict access.
        """
        return parse_payload(self.message_kind, self.payload)


__all__ = [
    "AgentResponsePayload",
    "ErrorCode",
    "ErrorPayload",
    "ExecutionRequestPayload",
    "ExecutionStatus",
    "MessageKind",
    "PlanRequestPayload",
    "QueueMessage",
    "QueuePayload",
    "ResearchConstraints",
    "Sender",
    "StatusPayload",
    "TaintLevel",
    "Urgency",
    "UserMessagePayload",
    "parse_payload",
]
