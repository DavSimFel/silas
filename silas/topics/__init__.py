"""Topics: topic detection, matching, and registry.

Public API — the types you need to define and work with Topics.
Internal implementation (proactivity engines, scheduler, UX metrics)
should be imported from their specific submodules.
"""

from silas.topics.matcher import TriggerMatcher
from silas.topics.model import (
    ApprovalSpec,
    EventSubscription,
    ReportingConfig,
    SoftTrigger,
    Topic,
    TriggerSpec,
)
from silas.topics.parser import parse_topic
from silas.topics.registry import TopicRegistry

__all__ = [
    "ApprovalSpec",
    "EventSubscription",
    "ReportingConfig",
    "SoftTrigger",
    "Topic",
    "TopicRegistry",
    "TriggerMatcher",
    "TriggerSpec",
    "parse_topic",
]
