"""Topic models — canonical re-exports from silas.topics.model.

This module provides the names required by the public API surface
(TopicFrontmatter, Topic, TopicTrigger, SoftTrigger, TopicApproval)
while keeping the implementation in ``silas.topics.model``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

# TopicFrontmatter is a view of Topic without body — useful when you only
# need metadata (e.g. listing topics without loading full content).
from pydantic import BaseModel, Field

from silas.topics.model import ApprovalSpec as TopicApproval
from silas.topics.model import SoftTrigger, Topic
from silas.topics.model import TriggerSpec as TopicTrigger


class TopicFrontmatter(BaseModel):
    """Frontmatter-only view of a Topic (no body)."""

    id: str
    name: str
    scope: Literal["session", "project", "infinite"]
    agent: Literal["proxy", "planner", "executor"]
    status: Literal["active", "paused", "completed", "archived"] = "active"
    triggers: list[TopicTrigger] = Field(default_factory=list)
    soft_triggers: list[SoftTrigger] = Field(default_factory=list)
    approvals: list[TopicApproval] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_topic(cls, topic: Topic) -> TopicFrontmatter:
        return cls.model_validate(topic.model_dump(exclude={"body"}))


__all__ = [
    "SoftTrigger",
    "Topic",
    "TopicApproval",
    "TopicFrontmatter",
    "TopicTrigger",
]
