"""TopicManager — load, match, and render topics as context."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from silas.topics.matcher import TriggerMatcher
from silas.topics.model import Topic
from silas.topics.parser import TopicParseError, parse_topic

logger = logging.getLogger(__name__)


@dataclass
class TopicMatch:
    """A matched topic with its relevance score."""

    topic: Topic
    score: float
    match_type: str  # "hard" | "soft"


class TopicManager:
    """Load topics from a directory, match against events/messages, render context.

    This is the high-level orchestrator that combines parsing, matching, and
    context rendering. The TopicRegistry handles CRUD; TopicManager handles
    the read-path used during message processing.
    """

    def __init__(self, topics_dir: Path) -> None:
        self._dir = topics_dir
        self._matcher = TriggerMatcher()
        self._topics: list[Topic] = []

    @property
    def topics(self) -> list[Topic]:
        return list(self._topics)

    def load(self) -> int:
        """Load all topic files from disk. Returns count of loaded topics."""
        self._topics.clear()
        if not self._dir.exists():
            return 0

        for path in sorted(self._dir.glob("*.md")):
            try:
                topic = parse_topic(path.read_text())
                self._topics.append(topic)
            except TopicParseError:
                logger.warning("Skipping unparseable topic: %s", path)
        return len(self._topics)

    def match_event(self, event: dict[str, Any]) -> list[TopicMatch]:
        """Match an event dict against hard triggers of all active topics.

        Returns matches sorted by specificity (more filters = higher score).
        """
        matches: list[TopicMatch] = []
        for topic in self._active_topics():
            if self._matcher.match_hard(event, topic.triggers):
                # Score by number of filter constraints — more specific = better.
                best_specificity = max(
                    (len(t.filter) + (1 if t.event else 0) for t in topic.triggers),
                    default=1,
                )
                matches.append(
                    TopicMatch(topic=topic, score=float(best_specificity), match_type="hard")
                )
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches

    def match_message(self, text: str) -> list[TopicMatch]:
        """Match message text against soft triggers of all active topics.

        Returns matches sorted by score descending.
        """
        matches: list[TopicMatch] = []
        for topic in self._active_topics():
            score = self._matcher.match_soft(text, topic.soft_triggers)
            if score > 0.0:
                matches.append(TopicMatch(topic=topic, score=score, match_type="soft"))
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches

    def get_context(self, topic: Topic) -> str:
        """Render a topic as injectable context markdown."""
        lines = [
            f"## Topic: {topic.name}",
            f"*Scope: {topic.scope} | Agent: {topic.agent} | Status: {topic.status}*",
            "",
            topic.body,
        ]
        return "\n".join(lines)

    def get_context_for_matches(self, matches: list[TopicMatch], max_topics: int = 3) -> str:
        """Render context for the top N matched topics."""
        if not matches:
            return ""

        blocks: list[str] = []
        for match in matches[:max_topics]:
            blocks.append(self.get_context(match.topic))
        return "\n\n---\n\n".join(blocks)

    def _active_topics(self) -> list[Topic]:
        return [t for t in self._topics if t.status == "active"]


__all__ = ["TopicManager", "TopicMatch"]
