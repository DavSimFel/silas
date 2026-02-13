"""Pydantic models for UX quality metrics (§0.5.6).

Raw event types and the summary model live here so the collector
stays decoupled from serialisation concerns.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ApprovalEvent(BaseModel):
    """Single approval/decline recorded with wall-clock timing."""

    token_id: str
    decision: str  # "approved" or "declined"
    duration_ms: int  # how long the user deliberated
    recorded_at: datetime


class BatchEvent(BaseModel):
    """Interaction cost for one batch review."""

    batch_id: str
    taps: int
    items: int
    recorded_at: datetime


class UXMetricsSummary(BaseModel):
    """Aggregated UX health snapshot over a time window."""

    window_hours: int
    total_decisions: int = 0
    median_decision_time_ms: float = 0.0
    approval_rate: float = 0.0
    decline_rate: float = 0.0
    batch_taps_per_item: float = 0.0
    decisions_per_hour: float = 0.0
    # 0.0 = no fatigue detected, 1.0 = severe slowdown trend
    fatigue_score: float = Field(default=0.0, ge=0.0, le=1.0)


__all__ = [
    "ApprovalEvent",
    "BatchEvent",
    "UXMetricsSummary",
]
