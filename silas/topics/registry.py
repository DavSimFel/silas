"""Filesystem-backed topic registry."""

from __future__ import annotations

import logging
from pathlib import Path

from silas.topics.matcher import TriggerMatcher
from silas.topics.model import Topic
from silas.topics.parser import TopicParseError, parse_topic, topic_to_markdown

logger = logging.getLogger(__name__)


class TopicRegistry:
    """CRUD registry for topics stored as markdown files on disk."""

    def __init__(self, topics_dir: Path) -> None:
        self._dir = topics_dir
        self._matcher = TriggerMatcher()

    def _path_for(self, topic_id: str) -> Path:
        return self._dir / f"{topic_id}.md"

    async def create(self, markdown: str) -> Topic:
        """Parse markdown and persist as a new topic file."""
        topic = parse_topic(markdown)
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(topic.id)
        path.write_text(topic_to_markdown(topic))
        return topic

    async def get(self, topic_id: str) -> Topic | None:
        """Load a topic by ID, or return None if not found."""
        path = self._path_for(topic_id)
        if not path.exists():
            return None
        try:
            return parse_topic(path.read_text())
        except TopicParseError:
            logger.warning("Failed to parse topic file: %s", path)
            return None

    async def list(self, status: str | None = None) -> list[Topic]:
        """List all topics, optionally filtered by status."""
        if not self._dir.exists():
            return []
        topics: list[Topic] = []
        for path in sorted(self._dir.glob("*.md")):
            try:
                topic = parse_topic(path.read_text())
            except TopicParseError:
                logger.warning("Skipping unparseable topic: %s", path)
                continue
            if status is None or topic.status == status:
                topics.append(topic)
        return topics

    async def update(self, topic_id: str, markdown: str) -> Topic:
        """Update an existing topic with new markdown content."""
        path = self._path_for(topic_id)
        if not path.exists():
            raise FileNotFoundError(f"Topic {topic_id} not found")
        topic = parse_topic(markdown)
        if topic.id != topic_id:
            raise ValueError(f"Topic ID mismatch: expected {topic_id}, got {topic.id}")
        path.write_text(topic_to_markdown(topic))
        return topic

    async def delete(self, topic_id: str) -> bool:
        """Delete a topic file. Returns True if it existed."""
        path = self._path_for(topic_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    async def find_by_trigger(self, source: str, event: str, context: dict) -> list[Topic]:
        """Find all active topics matching a hard trigger."""
        all_topics = await self.list(status="active")
        matched: list[Topic] = []
        full_event = {"source": source, "event": event, **context}
        for topic in all_topics:
            if self._matcher.match_hard(full_event, topic.triggers):
                matched.append(topic)
        return matched

    async def find_by_keywords(self, text: str) -> list[Topic]:
        """Find active topics whose soft triggers match the given text."""
        all_topics = await self.list(status="active")
        scored: list[tuple[float, Topic]] = []
        for topic in all_topics:
            score = self._matcher.match_soft(text, topic.soft_triggers)
            if score > 0.0:
                scored.append((score, topic))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored]
