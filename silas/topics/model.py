"""Topic models for the Silas topics system."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TriggerSpec(BaseModel):
    """Hard trigger specification for matching incoming events."""

    source: str
    event: str | None = None
    filter: dict[str, Any] = Field(default_factory=dict)
    expr: str | None = None


class SoftTrigger(BaseModel):
    """Soft trigger for keyword/entity-based matching."""

    keywords: list[str] = Field(default_factory=list)
    entity: str | None = None


class ApprovalSpec(BaseModel):
    """Approval requirement for tool usage within a topic."""

    tool: str
    constraints: dict[str, Any] = Field(default_factory=dict)


class Topic(BaseModel):
    """A topic represents a unit of work with triggers, scope, and lifecycle."""

    id: str
    name: str
    scope: Literal["session", "project", "infinite"]
    agent: Literal["proxy", "planner", "executor"]
    status: Literal["active", "paused", "completed", "archived"] = "active"
    triggers: list[TriggerSpec] = Field(default_factory=list)
    soft_triggers: list[SoftTrigger] = Field(default_factory=list)
    approvals: list[ApprovalSpec] = Field(default_factory=list)
    body: str
    created_at: datetime
    updated_at: datetime
