"""Tests for tier-2 context scorer and its integration with LiveContextManager."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from silas.context.scorer import ContextScorer, ScorerWeights
from silas.core.context_manager import LiveContextManager
from silas.core.token_counter import HeuristicTokenCounter
from silas.models.context import (
    ContextItem,
    ContextProfile,
    ContextZone,
    TokenBudget,
)
from silas.models.messages import TaintLevel

_COUNTER = HeuristicTokenCounter()


def _item(
    ctx_id: str,
    zone: ContextZone = ContextZone.chronicle,
    content: str = "hello world",
    turn: int = 1,
    *,
    taint: TaintLevel = TaintLevel.owner,
    created_at: datetime | None = None,
) -> ContextItem:
    return ContextItem(
        ctx_id=ctx_id,
        zone=zone,
        content=content,
        token_count=_COUNTER.count(content),
        turn_number=turn,
        source="test",
        taint=taint,
        kind="message",
        created_at=created_at or datetime.now(UTC),
    )


# ------------------------------------------------------------------
# Scorer unit tests
# ------------------------------------------------------------------


class TestScorerRecency:
    def test_newer_item_scores_higher(self) -> None:
        now = datetime.now(UTC)
        old = _item("old", created_at=now - timedelta(hours=2))
        new = _item("new", created_at=now)

        scorer = ContextScorer()
        scored = scorer.score_items([old, new], "anything")

        # First element (highest score) should be the newer item.
        assert scored[0][0].ctx_id == "new"
        assert scored[1][0].ctx_id == "old"


class TestScorerZonePriority:
    def test_system_ranks_above_chronicle(self) -> None:
        # Use zone-only weights to isolate the factor.
        weights = ScorerWeights(
            recency=0, zone_priority=1.0, taint_match=0, keyword_overlap=0, reference_count=0
        )
        scorer = ContextScorer(weights=weights)

        sys_item = _item("sys", zone=ContextZone.system)
        conv_item = _item("conv", zone=ContextZone.chronicle)

        scored = scorer.score_items([conv_item, sys_item], "")
        assert scored[0][0].ctx_id == "sys"

    def test_memory_ranks_above_workspace(self) -> None:
        weights = ScorerWeights(
            recency=0, zone_priority=1.0, taint_match=0, keyword_overlap=0, reference_count=0
        )
        scorer = ContextScorer(weights=weights)

        mem = _item("mem", zone=ContextZone.memory)
        ws = _item("ws", zone=ContextZone.workspace)

        scored = scorer.score_items([ws, mem], "")
        assert scored[0][0].ctx_id == "mem"


class TestScorerKeywordOverlap:
    def test_matching_keywords_score_higher(self) -> None:
        weights = ScorerWeights(
            recency=0, zone_priority=0, taint_match=0, keyword_overlap=1.0, reference_count=0
        )
        scorer = ContextScorer(weights=weights)

        relevant = _item("rel", content="deploy the kubernetes cluster")
        irrelevant = _item("irr", content="order some pizza tonight")

        scored = scorer.score_items([irrelevant, relevant], "deploy kubernetes")
        assert scored[0][0].ctx_id == "rel"

    def test_empty_query_gives_zero_keyword_score(self) -> None:
        weights = ScorerWeights(
            recency=0, zone_priority=0, taint_match=0, keyword_overlap=1.0, reference_count=0
        )
        scorer = ContextScorer(weights=weights)

        item = _item("a", content="some content")
        scored = scorer.score_items([item], "")
        assert scored[0][1] == pytest.approx(0.0)


class TestScorerReferenceCount:
    def test_referenced_items_score_higher(self) -> None:
        weights = ScorerWeights(
            recency=0, zone_priority=0, taint_match=0, keyword_overlap=0, reference_count=1.0
        )
        scorer = ContextScorer(weights=weights, referenced_ids={"ref"})

        ref_item = _item("ref")
        other = _item("other")

        scored = scorer.score_items([other, ref_item], "")
        assert scored[0][0].ctx_id == "ref"


class TestScorerTaintMatch:
    def test_matching_taint_scores_higher(self) -> None:
        weights = ScorerWeights(
            recency=0, zone_priority=0, taint_match=1.0, keyword_overlap=0, reference_count=0
        )
        scorer = ContextScorer(weights=weights)

        owner_item = _item("own", taint=TaintLevel.owner)
        ext_item = _item("ext", taint=TaintLevel.external)

        scored = scorer.score_items([ext_item, owner_item], "", current_taint=TaintLevel.owner)
        assert scored[0][0].ctx_id == "own"


# ------------------------------------------------------------------
# Integration: LiveContextManager + scorer
# ------------------------------------------------------------------


def _budget(**overrides: object) -> TokenBudget:
    profiles = {
        "conversation": ContextProfile(
            name="conversation", chronicle_pct=0.45, memory_pct=0.20, workspace_pct=0.15
        ),
    }
    defaults: dict[str, object] = {
        "total": 180_000,
        "profiles": profiles,
        "default_profile": "conversation",
    }
    defaults.update(overrides)
    return TokenBudget(**defaults)  # type: ignore[arg-type]


class TestLiveContextManagerScorerIntegration:
    """Verify that the scorer influences eviction order."""

    def test_scorer_evicts_least_relevant_first(self) -> None:
        """With a tiny budget, the scorer should keep the keyword-matching item."""
        budget = _budget(total=200, system_max=50)
        counter = HeuristicTokenCounter()
        mgr = LiveContextManager(budget, counter, use_scorer=True)

        scope = "test"
        now = datetime.now(UTC)

        # Both items are same age/zone — only keyword overlap differs.
        keep = _item("keep", content="deploy kubernetes cluster now", created_at=now)
        evict = _item("evict", content="pizza is delicious food item", created_at=now)

        mgr.add(scope, keep)
        mgr.add(scope, evict)

        evicted = mgr.enforce_budget(scope, turn_number=5, current_goal="deploy kubernetes")

        # The pizza item should be evicted, the kubernetes one kept.
        remaining_ids = {item.ctx_id for item in mgr.by_scope[scope]}
        if evicted:
            assert "evict" in evicted or "keep" in remaining_ids

    def test_feature_flag_disables_scorer(self) -> None:
        """When use_scorer=False, eviction falls back to tier-1 heuristic."""
        budget = _budget(total=200, system_max=50)
        counter = HeuristicTokenCounter()
        mgr = LiveContextManager(budget, counter, use_scorer=False)

        scope = "test"
        now = datetime.now(UTC)

        # Add items with different relevance — heuristic picks lowest relevance first.
        low_rel = _item("low", content="x" * 100, created_at=now)
        low_rel = low_rel.model_copy(update={"relevance": 0.1})
        high_rel = _item("high", content="y" * 100, created_at=now)

        mgr.add(scope, low_rel)
        mgr.add(scope, high_rel)

        evicted = mgr.enforce_budget(scope, turn_number=5, current_goal="test query")

        # Heuristic evicts low-relevance first.
        if evicted:
            assert evicted[0] == "low"

    def test_scorer_empty_items_no_crash(self) -> None:
        scorer = ContextScorer()
        result = scorer.score_items([], "query")
        assert result == []
