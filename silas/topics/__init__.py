"""Topics system for Silas — Phase 1."""

from silas.topics.matcher import TriggerMatcher
from silas.topics.model import Topic
from silas.topics.parser import parse_topic
from silas.topics.registry import TopicRegistry

__all__ = ["Topic", "TopicRegistry", "TriggerMatcher", "parse_topic"]
