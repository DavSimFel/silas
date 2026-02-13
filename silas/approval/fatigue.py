"""Approval fatigue mitigation (§0.5.4).

Adapts approval behaviour based on decision cadence and timing trends
so the human doesn't rubber-stamp under cognitive load.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field

from silas.models.approval import ApprovalScope

# Scopes that carry higher blast-radius — never auto-approve these.
_HIGH_RISK_SCOPES: frozenset[ApprovalScope] = frozenset(
    {
        ApprovalScope.self_update,
        ApprovalScope.credential_use,
        ApprovalScope.budget,
        ApprovalScope.full_plan,
        ApprovalScope.skill_install,
    }
)

# Volume thresholds per window (default 30 min)
_LOW_VOLUME_CEILING = 10
_MEDIUM_VOLUME_CEILING = 25

# Decision-time trend thresholds (proportion increase from first to second half)
_MEDIUM_TREND_THRESHOLD = 0.20
_HIGH_TREND_THRESHOLD = 0.50


class FatigueLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


Recommendation = Literal[
    "normal",
    "batch_more",
    "auto_approve_low_risk",
    "pause_and_summarize",
]


class DecisionRecord(BaseModel):
    """One resolved approval decision with timing info."""

    decided_at: datetime
    decision_time_ms: float = Field(ge=0)
    scope: ApprovalScope = ApprovalScope.single_step


class FatigueAnalysis(BaseModel):
    fatigue_level: FatigueLevel
    recommendation: Recommendation
    median_decision_time_ms: float
    decisions_in_window: int


def _compute_trend(decision_times: list[float]) -> float:
    """Return proportional increase from first half median to second half median.

    Positive values mean the human is slowing down (classic fatigue signal).
    Returns 0.0 when there aren't enough samples to compare.
    """
    if len(decision_times) < 4:
        return 0.0
    mid = len(decision_times) // 2
    first_half = median(decision_times[:mid])
    second_half = median(decision_times[mid:])
    if first_half <= 0:
        return 0.0
    return (second_half - first_half) / first_half


class ApprovalFatigueMitigator:
    """Analyses recent approval decisions and recommends flow adaptations."""

    def analyze_fatigue(
        self,
        recent_decisions: list[DecisionRecord],
        *,
        window_minutes: int = 30,
    ) -> FatigueAnalysis:
        cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
        # Only consider decisions inside the rolling window
        windowed = [d for d in recent_decisions if d.decided_at >= cutoff]
        windowed.sort(key=lambda d: d.decided_at)

        count = len(windowed)
        times = [d.decision_time_ms for d in windowed]
        med_time = median(times) if times else 0.0
        trend = _compute_trend(times)

        level = self._classify(count, trend)
        recommendation = self._recommend(level)

        return FatigueAnalysis(
            fatigue_level=level,
            recommendation=recommendation,
            median_decision_time_ms=med_time,
            decisions_in_window=count,
        )

    def should_auto_approve(
        self,
        analysis: FatigueAnalysis,
        scope: ApprovalScope,
    ) -> bool:
        """High-fatigue auto-approve is only safe for low-risk scopes."""
        if analysis.fatigue_level != FatigueLevel.high:
            return False
        return scope not in _HIGH_RISK_SCOPES

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify(count: int, trend: float) -> FatigueLevel:
        # High if volume alone is extreme OR decision times are ballooning
        if count > _MEDIUM_VOLUME_CEILING or trend >= _HIGH_TREND_THRESHOLD:
            return FatigueLevel.high
        if count >= _LOW_VOLUME_CEILING or trend >= _MEDIUM_TREND_THRESHOLD:
            return FatigueLevel.medium
        return FatigueLevel.low

    @staticmethod
    def _recommend(level: FatigueLevel) -> Recommendation:
        if level == FatigueLevel.high:
            return "auto_approve_low_risk"
        if level == FatigueLevel.medium:
            return "batch_more"
        return "normal"


__all__ = [
    "ApprovalFatigueMitigator",
    "DecisionRecord",
    "FatigueAnalysis",
    "FatigueLevel",
]
