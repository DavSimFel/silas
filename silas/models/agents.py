from __future__ import annotations

from enum import Enum
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from silas.models.memory import MemoryType


class MemoryOpType(str, Enum):
    store = "store"
    update = "update"
    delete = "delete"
    link = "link"


class MemoryOp(BaseModel):
    op: MemoryOpType
    content: str | None = None
    memory_id: str | None = None
    memory_type: MemoryType = MemoryType.episode
    tags: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    link_to: str | None = None
    link_type: str | None = None

    @model_validator(mode="after")
    def _validate_required_fields(self) -> MemoryOp:
        if self.op == MemoryOpType.store:
            if self.content is None:
                raise ValueError("content is required for store")
        elif self.op == MemoryOpType.update:
            if self.memory_id is None or self.content is None:
                raise ValueError("memory_id and content are required for update")
        elif self.op == MemoryOpType.delete:
            if self.memory_id is None:
                raise ValueError("memory_id is required for delete")
        elif self.op == MemoryOpType.link:
            if self.memory_id is None or self.link_to is None or self.link_type is None:
                raise ValueError("memory_id, link_to, and link_type are required for link")
        return self


class MemoryQueryStrategy(str, Enum):
    semantic = "semantic"
    temporal = "temporal"
    session = "session"
    keyword = "keyword"


class MemoryQuery(BaseModel):
    strategy: MemoryQueryStrategy
    query: str
    max_results: int = 5
    max_tokens: int = 2000


class InteractionRegister(str, Enum):
    exploration = "exploration"
    execution = "execution"
    review = "review"
    status = "status"


class InteractionMode(str, Enum):
    default_and_offer = "default_and_offer"
    act_and_report = "act_and_report"
    confirm_only_when_required = "confirm_only_when_required"


class PlanActionType(str, Enum):
    propose = "propose"
    revise = "revise"
    execute_next = "execute_next"
    abort = "abort"


class PlanAction(BaseModel):
    action: PlanActionType
    plan_markdown: str | None = None
    continuation_of: str | None = None
    interaction_mode_override: InteractionMode | None = None


class AgentResponse(BaseModel):
    message: str
    memory_queries: list[MemoryQuery] = Field(default_factory=list)
    memory_ops: list[MemoryOp] = Field(default_factory=list)
    plan_action: PlanAction | None = None
    needs_approval: bool = True

    @model_validator(mode="after")
    def _validate_memory_query_limit(self) -> AgentResponse:
        if len(self.memory_queries) > 3:
            raise ValueError("memory_queries may not contain more than 3 entries")
        return self


class RouteDecision(BaseModel):
    route: Literal["direct", "planner"]
    reason: str
    response: AgentResponse | None = None
    interaction_register: InteractionRegister
    interaction_mode: InteractionMode
    continuation_of: str | None = None
    context_profile: str
    plan_actions: list[dict[str, object]] = Field(default_factory=list)

    _profile_registry: ClassVar[set[str]] = {
        "conversation",
        "coding",
        "research",
        "support",
        "planning",
    }

    @classmethod
    def configure_profiles(cls, profile_names: set[str] | list[str]) -> None:
        cls._profile_registry = set(profile_names)

    @model_validator(mode="after")
    def _validate_route_shape(self) -> RouteDecision:
        if not self.context_profile.strip():
            raise ValueError("context_profile must be non-empty")
        if self._profile_registry and self.context_profile not in self._profile_registry:
            raise ValueError(f"unknown context profile: {self.context_profile}")

        if self.route == "direct" and self.response is None:
            raise ValueError("response is required when route='direct'")
        if self.route == "planner" and self.response is not None:
            raise ValueError("response must be None when route='planner'")
        if self.route == "direct" and self.plan_actions:
            raise ValueError("plan_actions are only valid when route='planner'")
        return self


__all__ = [
    "MemoryOpType",
    "MemoryOp",
    "MemoryQueryStrategy",
    "MemoryQuery",
    "InteractionRegister",
    "InteractionMode",
    "PlanActionType",
    "PlanAction",
    "AgentResponse",
    "RouteDecision",
]
