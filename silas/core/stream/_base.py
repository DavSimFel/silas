"""StreamBase — shared attribute protocol for Stream mixins.

Declares all instance attributes that mixin classes access via ``self``,
allowing Pyright to resolve types without seeing the concrete Stream
dataclass.  Mixins inherit from this protocol under TYPE_CHECKING so
there is zero runtime cost.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from silas.channels.base import ChannelAdapterCore
    from silas.connections.manager import SilasConnectionManager as ConnectionManager
    from silas.core.approval_flow import ApprovalFlow
    from silas.core.context_manager import LiveContextManager
    from silas.core.plan_parser import PlanParser
    from silas.core.turn_context import TurnContext
    from silas.gates import SilasGateRunner
    from silas.models.agents import RouteDecision
    from silas.models.context import ContextItem
    from silas.models.proactivity import SuggestionProposal
    from silas.models.work import WorkItem
    from silas.persistence.work_item_store import SQLiteWorkItemStore as WorkItemStore
    from silas.proactivity.calibrator import SimpleAutonomyCalibrator as AutonomyCalibrator
    from silas.proactivity.suggestions import SimpleSuggestionEngine as SuggestionEngine
    from silas.scheduler.ap_scheduler import SilasScheduler as TaskScheduler
    from silas.tools.approval_required import ApprovalRequiredToolset


class StreamBase(Protocol):
    """Protocol declaring shared attributes accessed by Stream mixins."""

    # ── Public dataclass fields ────────────────────────────────────
    channel: ChannelAdapterCore
    turn_context: TurnContext
    context_manager: LiveContextManager | None
    channels: tuple[ChannelAdapterCore, ...] | list[ChannelAdapterCore] | None
    scheduler: TaskScheduler | None
    plan_parser: PlanParser | None
    work_item_store: WorkItemStore | None
    connection_manager: ConnectionManager | None
    suggestion_engine: SuggestionEngine | None
    autonomy_calibrator: AutonomyCalibrator | None
    owner_id: str
    default_context_profile: str
    output_gate_runner: SilasGateRunner | None
    session_id: str | None

    # ── Private fields ─────────────────────────────────────────────
    _approval_flow: ApprovalFlow | None
    _pending_persona_scopes: set[str]
    _turn_processors: dict[str, object]
    _connection_locks: dict[str, object]
    _active_turn_context: ContextVar[TurnContext | None]
    _active_session_id: ContextVar[str | None]
    _multi_connection_mode: bool
    _signing_key: object
    _nonce_store: object

    # ── Cross-mixin methods (HelpersMixin) ─────────────────────────
    def _turn_context(self) -> TurnContext: ...
    async def _audit(self, event: str, **data: object) -> None: ...
    def _config_value(self, *path: str, default: object | None = None) -> object | None: ...
    def _get_context_manager(self) -> LiveContextManager | None: ...
    def _get_suggestion_engine(self) -> SuggestionEngine | None: ...
    def _get_autonomy_calibrator(self) -> AutonomyCalibrator | None: ...
    def _ensure_session_id(self) -> str: ...
    def _known_scopes(self) -> list[str]: ...
    def _constitution_content(self) -> str: ...
    def _tool_descriptions_content(self) -> str: ...
    def _configuration_content(self) -> str: ...
    def _rehydration_max_chronicle_entries(self) -> int: ...
    def _observation_mask_after_turns(self) -> int: ...
    def _masked_if_stale(
        self, item: ContextItem, latest_turn: int, mask_after_turns: int
    ) -> ContextItem: ...
    def _replace_context_item(
        self, cm: LiveContextManager, scope_id: str, item: ContextItem
    ) -> None: ...
    def _file_subscription_targets(self, item: WorkItem) -> tuple[str, ...]: ...
    def _prepend_high_confidence_suggestions(
        self, response_text: str, suggestions: list[SuggestionProposal]
    ) -> str: ...
    async def _push_suggestion_to_side_panel(
        self, connection_id: str, suggestion: SuggestionProposal
    ) -> None: ...

    # ── Cross-mixin methods (ToolsetMixin) ─────────────────────────
    def _available_skill_names(self) -> list[str]: ...
    def _extract_plan_actions(self, routed: RouteDecision) -> list[dict[str, object]]: ...
    def _build_planner_prompt(
        self,
        message_text: str,
        rendered_context: str,
        *,
        toolset: ApprovalRequiredToolset | None = None,
    ) -> str: ...


__all__ = ["StreamBase"]
