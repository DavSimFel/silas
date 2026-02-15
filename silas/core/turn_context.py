from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from silas.approval.manager import LiveApprovalManager as ApprovalManager
    from silas.audit.sqlite_audit import SQLiteAuditLog as AuditLog
    from silas.core.context_manager import LiveContextManager
    from silas.gates.runner import SilasGateRunner as GateRunner
    from silas.memory.sqlite_store import SQLiteMemoryStore as MemoryStore
    from silas.persistence.chronicle_store import SQLiteChronicleStore as ChronicleStore
    from silas.personality.engine import SilasPersonalityEngine as PersonalityEngine
    from silas.proactivity.calibrator import SimpleAutonomyCalibrator as AutonomyCalibrator
    from silas.proactivity.suggestions import SimpleSuggestionEngine as SuggestionEngine
    from silas.skills.executor import SkillExecutor
    from silas.skills.registry import SilasSkillLoader as SkillLoader
    from silas.skills.registry import SkillRegistry
    from silas.tools.resolver import LiveSkillResolver as SkillResolver
    from silas.work.executor import LiveWorkItemExecutor as WorkItemExecutor


class StructuredAgentRunner(Protocol):
    async def run(self, prompt: str) -> object: ...


@dataclass(slots=True)
class TurnContext:
    scope_id: str = "owner"
    context_manager: LiveContextManager | None = None
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
