"""Tests for approval fatigue mitigation (§0.5.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from silas.approval.fatigue import (
    ApprovalFatigueMitigator,
    DecisionRecord,
    FatigueLevel,
)
from silas.models.approval import ApprovalScope


@pytest.fixture
def mitigator() -> ApprovalFatigueMitigator:
    return ApprovalFatigueMitigator()


def _make_decisions(
    count: int,
    *,
    base_time_ms: float = 500.0,
    trend_factor: float = 0.0,
    minutes_ago: float = 10.0,
    scope: ApprovalScope = ApprovalScope.single_step,
) -> list[DecisionRecord]:
    """Generate *count* decisions spread across the last *minutes_ago* minutes.

    trend_factor > 0 makes later decisions progressively slower,
    simulating fatigue-induced slowdown.
    """
    now = datetime.now(UTC)
    # Place decisions in a 2-minute band centred at minutes_ago in the past
    centre = now - timedelta(minutes=minutes_ago)
    start = centre - timedelta(minutes=1)
    span = timedelta(minutes=2)
    interval = span / max(count, 1)
    records: list[DecisionRecord] = []
    for i in range(count):
        # Linear ramp: first decision at base_time_ms, last at base*(1+trend_factor)
        progress = i / max(count - 1, 1)
        time_ms = base_time_ms * (1.0 + trend_factor * progress)
        records.append(
            DecisionRecord(
                decided_at=start + interval * i,
                decision_time_ms=time_ms,
                scope=scope,
            )
        )
    return records


# --- Fatigue level classification ---


class TestFatigueLevel:
    def test_low_fatigue_few_decisions(self, mitigator: ApprovalFatigueMitigator) -> None:
        decisions = _make_decisions(5)
        analysis = mitigator.analyze_fatigue(decisions)
        assert analysis.fatigue_level == FatigueLevel.low
        assert analysis.recommendation == "normal"
        assert analysis.decisions_in_window == 5

    def test_medium_fatigue_volume(self, mitigator: ApprovalFatigueMitigator) -> None:
        decisions = _make_decisions(15)
        analysis = mitigator.analyze_fatigue(decisions)
        assert analysis.fatigue_level == FatigueLevel.medium
        assert analysis.recommendation == "batch_more"

    def test_medium_fatigue_increasing_decision_time(
        self, mitigator: ApprovalFatigueMitigator
    ) -> None:
        # 8 decisions (below volume threshold) but 30% slowdown
        decisions = _make_decisions(8, trend_factor=0.6)
        analysis = mitigator.analyze_fatigue(decisions)
        # Trend ~30% puts us at medium or above
        assert analysis.fatigue_level in {FatigueLevel.medium, FatigueLevel.high}

    def test_high_fatigue_many_rapid_decisions(
        self, mitigator: ApprovalFatigueMitigator
    ) -> None:
        decisions = _make_decisions(30)
        analysis = mitigator.analyze_fatigue(decisions)
        assert analysis.fatigue_level == FatigueLevel.high
        assert analysis.recommendation == "auto_approve_low_risk"

    def test_high_fatigue_extreme_trend(
        self, mitigator: ApprovalFatigueMitigator
    ) -> None:
        # Few decisions but decision time doubles
        decisions = _make_decisions(6, trend_factor=1.2)
        analysis = mitigator.analyze_fatigue(decisions)
        assert analysis.fatigue_level == FatigueLevel.high


# --- Window filtering ---


class TestWindowFiltering:
    def test_old_decisions_excluded(self, mitigator: ApprovalFatigueMitigator) -> None:
        old = _make_decisions(30, minutes_ago=60)
        analysis = mitigator.analyze_fatigue(old, window_minutes=30)
        # All decisions are older than 30 minutes — should be filtered out
        assert analysis.decisions_in_window == 0
        assert analysis.fatigue_level == FatigueLevel.low

    def test_mixed_old_and_recent(self, mitigator: ApprovalFatigueMitigator) -> None:
        old = _make_decisions(20, minutes_ago=60)
        recent = _make_decisions(3, minutes_ago=5)
        analysis = mitigator.analyze_fatigue(old + recent, window_minutes=30)
        assert analysis.decisions_in_window == 3


# --- Recommendation / auto-approve logic ---


class TestAutoApprove:
    def test_high_fatigue_allows_low_risk_auto_approve(
        self, mitigator: ApprovalFatigueMitigator
    ) -> None:
        decisions = _make_decisions(30)
        analysis = mitigator.analyze_fatigue(decisions)
        assert mitigator.should_auto_approve(analysis, ApprovalScope.single_step)

    def test_high_fatigue_blocks_high_risk_auto_approve(
        self, mitigator: ApprovalFatigueMitigator
    ) -> None:
        decisions = _make_decisions(30)
        analysis = mitigator.analyze_fatigue(decisions)
        assert not mitigator.should_auto_approve(analysis, ApprovalScope.credential_use)
        assert not mitigator.should_auto_approve(analysis, ApprovalScope.self_update)
        assert not mitigator.should_auto_approve(analysis, ApprovalScope.budget)

    def test_low_fatigue_never_auto_approves(
        self, mitigator: ApprovalFatigueMitigator
    ) -> None:
        decisions = _make_decisions(3)
        analysis = mitigator.analyze_fatigue(decisions)
        assert not mitigator.should_auto_approve(analysis, ApprovalScope.single_step)


# --- Median decision time ---


class TestMedianTime:
    def test_median_computed_correctly(self, mitigator: ApprovalFatigueMitigator) -> None:
        decisions = _make_decisions(5, base_time_ms=200.0)
        analysis = mitigator.analyze_fatigue(decisions)
        assert analysis.median_decision_time_ms > 0

    def test_empty_decisions(self, mitigator: ApprovalFatigueMitigator) -> None:
        analysis = mitigator.analyze_fatigue([])
        assert analysis.median_decision_time_ms == 0.0
        assert analysis.decisions_in_window == 0
        assert analysis.fatigue_level == FatigueLevel.low
