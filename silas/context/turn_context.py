from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from silas.protocols.approval import ApprovalManager
from silas.protocols.audit import AuditLog
from silas.protocols.chronicle import ChronicleStore
from silas.protocols.context import ContextManager
from silas.protocols.gates import GateRunner
from silas.protocols.memory import MemoryStore
from silas.protocols.personality import PersonalityEngine
from silas.protocols.proactivity import AutonomyCalibrator, SuggestionEngine
from silas.protocols.skills import SkillLoader, SkillResolver
from silas.protocols.work import WorkItemExecutor

if TYPE_CHECKING:
    from silas.context.manager import LiveContextManager
    from silas.skills.executor import SkillExecutor
    from silas.skills.registry import SkillRegistry


class StructuredAgentRunner(Protocol):
    async def run(self, prompt: str) -> object: ...


@dataclass(slots=True)
class TurnContext:
    scope_id: str = "owner"
    context_manager: ContextManager | None = None
    live_context_manager: LiveContextManager | None = None
    memory_store: MemoryStore | None = None
    chronicle_store: ChronicleStore | None = None
    proxy: StructuredAgentRunner | None = None
    planner: StructuredAgentRunner | None = None
    work_executor: WorkItemExecutor | None = None
    gate_runner: GateRunner | None = None
    embedder: object | None = None
    personality_engine: PersonalityEngine | None = None
    skill_loader: SkillLoader | None = None
    skill_resolver: SkillResolver | None = None
    skill_registry: SkillRegistry | None = None
    skill_executor: SkillExecutor | None = None
    approval_manager: ApprovalManager | None = None
    suggestion_engine: SuggestionEngine | None = None
    autonomy_calibrator: AutonomyCalibrator | None = None
    audit: AuditLog | None = None
    config: object | None = None
    turn_number: int = 0


__all__ = ["StructuredAgentRunner", "TurnContext"]
