from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from silas.models.agents import InteractionMode
from silas.models.messages import utc_now

if TYPE_CHECKING:
    from silas.models.approval import ApprovalToken
    from silas.models.gates import AccessLevel, Gate


class WorkItemType(StrEnum):
    task = "task"
    project = "project"
    goal = "goal"


class WorkItemStatus(StrEnum):
    pending = "pending"
    running = "running"
    healthy = "healthy"
    done = "done"
    failed = "failed"
    stuck = "stuck"
    blocked = "blocked"
    paused = "paused"


class Budget(BaseModel):
    max_tokens: int = 200_000
    max_cost_usd: float = 2.0
    max_wall_time_seconds: int = 1_800
    max_attempts: int = 5
    max_planner_calls: int = 3


class BudgetUsed(BaseModel):
    tokens: int = 0
    cost_usd: float = 0.0
    wall_time_seconds: float = 0.0
    attempts: int = 0
    planner_calls: int = 0
    executor_runs: int = 0

    def exceeds(self, budget: Budget) -> bool:
        return (
            self.tokens >= budget.max_tokens
            or self.cost_usd >= budget.max_cost_usd
            or self.wall_time_seconds >= budget.max_wall_time_seconds
            or self.attempts >= budget.max_attempts
            or self.planner_calls >= budget.max_planner_calls
        )

    def merge(self, child: BudgetUsed) -> BudgetUsed:
        self.tokens += child.tokens
        self.cost_usd += child.cost_usd
        self.wall_time_seconds += child.wall_time_seconds
        self.attempts += child.attempts
        self.planner_calls += child.planner_calls
        self.executor_runs += child.executor_runs
        return self


class Expectation(BaseModel):
    exit_code: int | None = None
    equals: str | None = None
    contains: str | None = None
    regex: str | None = None
    output_lt: float | None = None
    output_gt: float | None = None
    file_exists: str | None = None
    not_empty: bool | None = None

    @model_validator(mode="after")
    def _validate_exactly_one_predicate(self) -> Expectation:
        checks = [
            self.exit_code is not None,
            self.equals is not None,
            self.contains is not None,
            self.regex is not None,
            self.output_lt is not None,
            self.output_gt is not None,
            self.file_exists is not None,
            self.not_empty is True,
        ]
        selected = sum(checks)
        if selected != 1:
            raise ValueError("Expectation must define exactly one predicate")
        return self


class VerificationCheck(BaseModel):
    name: str
    run: str
    expect: Expectation
    timeout: int = 60
    network: bool = False


class EscalationAction(BaseModel):
    action: str
    queue: str | None = None
    message: str | None = None
    instruction: str | None = None
    max_retries: int = 2
    fallback: str | None = None


class WorkItem(BaseModel):
    id: str
    type: WorkItemType
    title: str
    parent: str | None = None
    spawned_by: str | None = None
    follow_up_of: str | None = None
    domain: str | None = None

    agent: Literal["ephemeral", "stream"] = "ephemeral"
    budget: Budget = Field(default_factory=Budget)
    needs_approval: bool = True
    approval_token: ApprovalToken | None = None

    body: str
    interaction_mode: InteractionMode = InteractionMode.confirm_only_when_required
    input_artifacts_from: list[str] = Field(default_factory=list)

    verify: list[VerificationCheck] = Field(default_factory=list)
    gates: list[Gate] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    access_levels: dict[str, AccessLevel] = Field(default_factory=dict)

    escalation: dict[str, EscalationAction] = Field(default_factory=dict)
    schedule: str | None = None
    on_failure: str = "report"
    on_stuck: str = "consult_planner"
    failure_context: str | None = None

    tasks: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)

    status: WorkItemStatus = WorkItemStatus.pending
    attempts: int = 0
    budget_used: BudgetUsed = Field(default_factory=BudgetUsed)
    verification_results: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_agent_lane(self) -> WorkItem:
        if self.type in {WorkItemType.task, WorkItemType.project} and self.agent != "ephemeral":
            raise ValueError("task and project work items must use agent='ephemeral'")
        if self.type == WorkItemType.goal and self.schedule == "always_on" and self.agent == "ephemeral":
            self.agent = "stream"
        return self

    def plan_hash_bytes(self) -> bytes:
        projection = {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "parent": self.parent,
            "spawned_by": self.spawned_by,
            "follow_up_of": self.follow_up_of,
            "domain": self.domain,
            "agent": self.agent,
            "budget": self.budget.model_dump(mode="json"),
            "body": self.body,
            "interaction_mode": self.interaction_mode,
            "input_artifacts_from": self.input_artifacts_from,
            "verify": [check.model_dump(mode="json") for check in self.verify],
            "gates": [gate.model_dump(mode="json") for gate in self.gates],
            "skills": self.skills,
            "access_levels": {k: v.model_dump(mode="json") for k, v in self.access_levels.items()},
            "escalation": {k: v.model_dump(mode="json") for k, v in self.escalation.items()},
            "schedule": self.schedule,
            "on_failure": self.on_failure,
            "on_stuck": self.on_stuck,
            "failure_context": self.failure_context,
            "tasks": self.tasks,
            "depends_on": self.depends_on,
        }
        return json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def plan_hash(self) -> str:
        return sha256(self.plan_hash_bytes()).hexdigest()


class WorkItemResult(BaseModel):
    work_item_id: str
    status: WorkItemStatus
    summary: str
    last_error: str | None = None
    verification_results: list[dict[str, object]] = Field(default_factory=list)
    budget_used: BudgetUsed = Field(default_factory=BudgetUsed)
    artifacts: dict[str, str] = Field(default_factory=dict)
    next_steps: list[str] = Field(default_factory=list)


def work_item_plan_hash_bytes(work_item: WorkItem) -> bytes:
    return work_item.plan_hash_bytes()


def work_item_plan_hash(work_item: WorkItem) -> str:
    return work_item.plan_hash()


__all__ = [
    "Budget",
    "BudgetUsed",
    "EscalationAction",
    "Expectation",
    "VerificationCheck",
    "WorkItem",
    "WorkItemResult",
    "WorkItemStatus",
    "WorkItemType",
    "work_item_plan_hash",
    "work_item_plan_hash_bytes",
]
