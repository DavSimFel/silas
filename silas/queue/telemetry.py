"""Telemetry and audit event schemas for queue operations.

Defines structured event types emitted by the queue infrastructure for
observability. These are data containers only — the actual emission/storage
is handled by the telemetry sink (not yet implemented).

Spec reference: §2.4 (QueueTelemetryEvent) and §2.5 (RuntimeAuditEvent).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    """Timezone-aware UTC now for event timestamps."""
    return datetime.now(UTC)


# Why Literal for event types: these are a closed set defined by the spec.
# Using Literal catches typos at type-check time and documents the valid values.

QueueEventKind = Literal[
    "enqueue",
    "dequeue",
    "ack",
    "nack",
    "dead_letter",
    "heartbeat",
    "expired",
]

AuditEventKind = Literal[
    "enqueue",
    "dequeue",
    "approval",
    "verify",
    "check",
    "gate_block",
]


class QueueTelemetryEvent(BaseModel):
    """Structured telemetry event emitted by queue operations.

    Used for monitoring queue health: depth, wait times, lease durations.
    Each event captures a single observable moment in the queue lifecycle.

    Why a flat model with optional detail fields: telemetry events need to be
    cheap to create and easy to aggregate. Nested structures would complicate
    metric extraction.
    """

    queue_name: str
    event: QueueEventKind
    message_id: str
    trace_id: str
    timestamp: datetime = Field(default_factory=_utc_now)
    # Why optional detail fields instead of a generic dict: typed fields
    # enable downstream dashboards to query without JSON parsing.
    queue_depth: int | None = None
    wait_ms: float | None = None
    lease_duration_s: float | None = None


class RuntimeAuditEvent(BaseModel):
    """Audit trail event for security-relevant runtime actions.

    Captures who did what and when for compliance and debugging.
    Distinct from telemetry: audit events are about authorization
    and control flow, not performance metrics.
    """

    event: AuditEventKind
    trace_id: str
    agent: str
    message_id: str
    timestamp: datetime = Field(default_factory=_utc_now)
    detail: str | None = None


__all__ = [
    "AuditEventKind",
    "QueueEventKind",
    "QueueTelemetryEvent",
    "RuntimeAuditEvent",
]
