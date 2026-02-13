"""Tier-2 context scorer for smart eviction (Spec §5.7).

Scores context items by relevance so the eviction loop can drop the
least-valuable items first instead of relying on pure FIFO ordering.
No LLM calls — all heuristics are local and cheap enough to run every turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from silas.models.context import ContextItem, ContextZone
from silas.models.messages import TaintLevel

# Zone priority order — higher index = more important to keep.
_ZONE_PRIORITY: dict[ContextZone, float] = {
    ContextZone.system: 1.0,
    ContextZone.chronicle: 0.4,
    ContextZone.memory: 0.5,
    ContextZone.workspace: 0.3,
}

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


@dataclass(frozen=True)
class ScorerWeights:
    """Tunable knobs for each scoring factor.

    All weights are relative — they get normalised internally so their
    sum doesn't matter, only the ratios between them.
    """

    recency: float = 0.25
    zone_priority: float = 0.20
    taint_match: float = 0.15
    keyword_overlap: float = 0.25
    reference_count: float = 0.15


@dataclass
class ContextScorer:
    """Scores context items for eviction priority.

    Higher score → keep; lower score → evict first.
    """

    weights: ScorerWeights = field(default_factory=ScorerWeights)

    # Items the agent has explicitly referenced (ctx_ids).
    # Callers populate this as the conversation progresses.
    referenced_ids: set[str] = field(default_factory=set)

    def score_items(
        self,
        items: list[ContextItem],
        current_query: str,
        *,
        current_taint: TaintLevel = TaintLevel.owner,
    ) -> list[tuple[ContextItem, float]]:
        """Return (item, score) pairs sorted descending by score."""
        if not items:
            return []

        query_words = self._tokenize(current_query)
        now = datetime.now(UTC)

        # Pre-compute max age so recency is relative to the batch.
        ages = [(now - item.created_at).total_seconds() for item in items]
        max_age = max(ages) if ages else 1.0
        # Avoid division by zero when all items have the same timestamp.
        max_age = max(max_age, 1.0)

        scored: list[tuple[ContextItem, float]] = []
        for item, age_seconds in zip(items, ages, strict=True):
            score = self._score_one(item, age_seconds, max_age, query_words, current_taint)
            scored.append((item, score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Factor computations — each returns a value in [0.0, 1.0].
    # ------------------------------------------------------------------

    def _recency_factor(self, age_seconds: float, max_age: float) -> float:
        """Newer items score higher. Linear decay from 1.0 → 0.0."""
        return 1.0 - (age_seconds / max_age)

    def _zone_factor(self, zone: ContextZone) -> float:
        return _ZONE_PRIORITY.get(zone, 0.0)

    def _taint_factor(self, item_taint: TaintLevel, current_taint: TaintLevel) -> float:
        """Matching taint = 1.0, owner always gets a boost, otherwise 0.0."""
        if item_taint == current_taint:
            return 1.0
        # Owner-tainted content is always somewhat relevant.
        if item_taint == TaintLevel.owner:
            return 0.5
        return 0.0

    def _keyword_factor(self, item: ContextItem, query_words: set[str]) -> float:
        """Jaccard-ish overlap between query terms and item content."""
        if not query_words:
            return 0.0
        item_words = self._tokenize(item.content)
        if not item_words:
            return 0.0
        overlap = len(query_words & item_words)
        # Normalise by query size so longer items don't dominate.
        return min(overlap / len(query_words), 1.0)

    def _reference_factor(self, ctx_id: str) -> float:
        """Items the agent has referenced are more valuable."""
        return 1.0 if ctx_id in self.referenced_ids else 0.0

    # ------------------------------------------------------------------

    def _score_one(
        self,
        item: ContextItem,
        age_seconds: float,
        max_age: float,
        query_words: set[str],
        current_taint: TaintLevel,
    ) -> float:
        w = self.weights
        total_weight = w.recency + w.zone_priority + w.taint_match + w.keyword_overlap + w.reference_count
        if total_weight == 0.0:
            return 0.0

        raw = (
            w.recency * self._recency_factor(age_seconds, max_age)
            + w.zone_priority * self._zone_factor(item.zone)
            + w.taint_match * self._taint_factor(item.taint, current_taint)
            + w.keyword_overlap * self._keyword_factor(item, query_words)
            + w.reference_count * self._reference_factor(item.ctx_id)
        )
        return raw / total_weight

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Extract lowercased word tokens for keyword matching."""
        return {m.group().lower() for m in _WORD_RE.finditer(text)}


__all__ = ["ContextScorer", "ScorerWeights"]
