"""Trigger matching logic for topics."""

from __future__ import annotations

from typing import Any

from silas.topics.model import SoftTrigger, TriggerSpec


class TriggerMatcher:
    """Match incoming events and text against topic triggers."""

    def match_hard(self, event: dict[str, Any], triggers: list[TriggerSpec]) -> bool:
        """Return True if the event matches any hard trigger."""
        for trigger in triggers:
            if event.get("source") != trigger.source:
                continue
            if trigger.event is not None and event.get("event") != trigger.event:
                continue
            if not self._filters_match(event, trigger.filter):
                continue
            return True
        return False

    def match_soft(self, text: str, soft_triggers: list[SoftTrigger]) -> float:
        """Score how well text matches soft triggers. 0.0-1.0."""
        if not soft_triggers:
            return 0.0

        text_lower = text.lower()
        best_score = 0.0

        for st in soft_triggers:
            score = self._score_soft_trigger(text_lower, st)
            best_score = max(best_score, score)

        return best_score

    def _filters_match(self, event: dict[str, Any], filters: dict[str, Any]) -> bool:
        """Check that all filter key-value pairs match the event."""
        return all(event.get(key) == value for key, value in filters.items())

    def _score_soft_trigger(self, text_lower: str, st: SoftTrigger) -> float:
        """Score a single soft trigger against lowercased text."""
        scores: list[float] = []

        if st.keywords:
            matched = sum(1 for kw in st.keywords if kw.lower() in text_lower)
            scores.append(matched / len(st.keywords))

        if st.entity is not None:
            scores.append(1.0 if st.entity.lower() in text_lower else 0.0)

        if not scores:
            return 0.0
        return sum(scores) / len(scores)
