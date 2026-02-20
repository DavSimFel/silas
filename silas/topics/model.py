"""Topic models for the Silas topics system.

A Topic is the single unit of intent in Silas. What a Topic *does* depends
on which optional fields are populated:

  - Has ``subscriptions``?       → reacts to external events automatically
  - Has ``schedule``?            → runs periodically (cron / interval / once)
  - Has ``standing_approvals``?  → can act autonomously without per-run approval
  - Has ``work_template``?       → knows what to do when activated
  - Has none of the above?       → manually activated by the user

There is no separate Goal model. A "goal" is simply a Topic that has a
schedule and/or standing_approvals configured.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from silas.models.goals import Schedule, StandingApproval


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


class EventSubscription(BaseModel):
    """An autonomous event subscription that activates a Topic.

    Unlike ``TriggerSpec`` (used for routing/matching incoming messages),
    an ``EventSubscription`` describes an external event source that the
    runtime actively monitors and uses to wake up the Topic without user
    input.
    """

    source: str
    event: str | None = None
    filter: dict[str, Any] = Field(default_factory=dict)


class ReportingConfig(BaseModel):
    """Optional reporting behaviour when a Topic completes a run."""

    channel: str
    format: str = "text"
    include_result: bool = True


class Topic(BaseModel):
    """A topic represents a unit of work with triggers, scope, and lifecycle.

    Goal-like behaviour is expressed through optional fields — no separate
    Goal model is needed.
    """

    id: str
    name: str
    scope: Literal["session", "project", "infinite"]
    agent: Literal["proxy", "planner", "executor"]
    status: Literal["active", "paused", "completed", "archived"] = "active"
    triggers: list[TriggerSpec] = Field(default_factory=list)
    soft_triggers: list[SoftTrigger] = Field(default_factory=list)
    approvals: list[ApprovalSpec] = Field(default_factory=list)
    body: str

    # ── Goal / autonomous-behaviour fields ──────────────────────────────
    # A Topic with any of these populated behaves like what used to be
    # called a "Goal".  No type discriminator is needed.
    subscriptions: list[EventSubscription] = Field(default_factory=list)
    schedule: Schedule | None = None
    standing_approvals: list[StandingApproval] = Field(default_factory=list)
    reporting: ReportingConfig | None = None
    work_template: dict[str, Any] = Field(default_factory=dict)
    urgency: str = "background"

    created_at: datetime
    updated_at: datetime
