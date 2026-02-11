from __future__ import annotations

from silas.models.agents import (
    AgentResponse,
    InteractionMode,
    InteractionRegister,
    MemoryOp,
    MemoryOpType,
    MemoryQuery,
    MemoryQueryStrategy,
    PlanAction,
    PlanActionType,
    RouteDecision,
)
from silas.models.approval import (
    ApprovalDecision,
    ApprovalScope,
    ApprovalToken,
    ApprovalVerdict,
    Base64Bytes,
    PendingApproval,
)
from silas.models.context import (
    ContextItem,
    ContextProfile,
    ContextSubscription,
    ContextZone,
    TokenBudget,
)
from silas.models.gates import (
    ALLOWED_MUTATIONS,
    AccessLevel,
    Gate,
    GateLane,
    GateProvider,
    GateResult,
    GateTrigger,
    GateType,
)
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import ChannelMessage, SignedMessage, TaintLevel
from silas.models.personality import (
    AxisProfile,
    MoodState,
    PersonaEvent,
    PersonaPreset,
    PersonaState,
    VoiceConfig,
)
from silas.models.proactivity import Suggestion, SuggestionProposal
from silas.models.sessions import Session, SessionType
from silas.models.skills import SkillDefinition, SkillMetadata, SkillRef, SkillResult
from silas.models.work import (
    Budget,
    BudgetUsed,
    EscalationAction,
    Expectation,
    VerificationCheck,
    WorkItem,
    WorkItemResult,
    WorkItemStatus,
    WorkItemType,
)

WorkItem.model_rebuild(
    _types_namespace={
        "ApprovalToken": ApprovalToken,
        "Gate": Gate,
        "AccessLevel": AccessLevel,
    }
)

__all__ = [
    "TaintLevel",
    "ChannelMessage",
    "SignedMessage",
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
    "MemoryType",
    "MemoryItem",
    "ContextZone",
    "ContextProfile",
    "ContextItem",
    "ContextSubscription",
    "TokenBudget",
    "WorkItemType",
    "WorkItemStatus",
    "Budget",
    "BudgetUsed",
    "Expectation",
    "VerificationCheck",
    "EscalationAction",
    "WorkItem",
    "WorkItemResult",
    "GateType",
    "GateLane",
    "GateProvider",
    "GateTrigger",
    "Gate",
    "AccessLevel",
    "GateResult",
    "ALLOWED_MUTATIONS",
    "ApprovalScope",
    "ApprovalVerdict",
    "ApprovalDecision",
    "Base64Bytes",
    "ApprovalToken",
    "PendingApproval",
    "Session",
    "SessionType",
    "AxisProfile",
    "MoodState",
    "VoiceConfig",
    "PersonaPreset",
    "PersonaState",
    "PersonaEvent",
    "Suggestion",
    "SuggestionProposal",
    "SkillDefinition",
    "SkillMetadata",
    "SkillRef",
    "SkillResult",
]
