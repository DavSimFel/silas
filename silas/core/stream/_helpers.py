"""HelpersMixin — config access, content rendering, audit, and accessors."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from silas.core.token_counter import HeuristicTokenCounter
from silas.models.context import ContextItem
from silas.models.proactivity import SuggestionProposal
from silas.models.work import WorkItem

if TYPE_CHECKING:
    from silas.core.context_manager import LiveContextManager
    from silas.protocols.proactivity import AutonomyCalibrator, SuggestionEngine

_counter = HeuristicTokenCounter()


class HelpersMixin:
    """Foundation mixin: audit, config, content helpers, and accessor shortcuts."""

    async def _ensure_persona_state_loaded(self, scope_id: str) -> None:
        if scope_id not in self._pending_persona_scopes:
            return

        personality_engine = self._turn_context().personality_engine
        if personality_engine is None:
            self._pending_persona_scopes.discard(scope_id)
            await self._audit("stream_startup_dependency_missing", dependency="personality_engine")
            return

        try:
            await personality_engine.get_effective_axes(scope_id, "default")
        except (RuntimeError, ValueError, OSError) as exc:
            await self._audit("persona_state_lazy_load_failed", scope_id=scope_id, error=str(exc))
        finally:
            self._pending_persona_scopes.discard(scope_id)

    def _known_scopes(self) -> list[str]:
        scopes = {self._turn_context().scope_id}
        scopes.update(
            processor.turn_context.scope_id
            for processor in self._turn_processors.values()
        )

        context_manager = self._get_context_manager()
        if context_manager is not None:
            by_scope = getattr(context_manager, "by_scope", None)
            if isinstance(by_scope, dict):
                scopes.update(
                    scope_id for scope_id in by_scope
                    if isinstance(scope_id, str) and scope_id.strip()
                )

        chronicle_store = self._turn_context().chronicle_store
        if chronicle_store is not None:
            for attr_name in ("by_scope", "known_scopes", "scopes"):
                raw = getattr(chronicle_store, attr_name, None)
                if isinstance(raw, (dict, list, tuple, set)):
                    scopes.update(
                        scope_id for scope_id in raw
                        if isinstance(scope_id, str) and scope_id.strip()
                    )

        return sorted(scopes)

    def _config_value(self, *path: str, default: object | None = None) -> object | None:
        current: object | None = self._turn_context().config
        for key in path:
            if current is None:
                return default
            if isinstance(current, dict):
                current = current.get(key)
                continue
            current = getattr(current, key, None)
        if current is None:
            return default
        return current

    def _rehydration_max_chronicle_entries(self) -> int:
        value = self._config_value("rehydration", "max_chronicle_entries", default=50)
        if not isinstance(value, int) or value < 1:
            return 50
        return value

    def _observation_mask_after_turns(self) -> int:
        value = self._config_value("context", "observation_mask_after_turns", default=5)
        if not isinstance(value, int) or value < 0:
            return 5
        return value

    def _masked_if_stale(
        self,
        item: ContextItem,
        latest_turn: int,
        mask_after_turns: int,
    ) -> ContextItem:
        if item.kind != "tool_result" or item.masked:
            return item
        if latest_turn - item.turn_number <= mask_after_turns:
            return item

        placeholder = (
            f"[Result of {item.source} — {item.token_count} tokens — see memory for details]"
        )
        return item.model_copy(
            update={
                "content": placeholder,
                "token_count": _counter.count(placeholder),
                "masked": True,
            }
        )

    def _replace_context_item(
        self,
        cm: LiveContextManager,
        scope_id: str,
        item: ContextItem,
    ) -> None:
        cm.drop(scope_id, item.ctx_id)
        cm.add(scope_id, item)

    def _constitution_content(self) -> str:
        raw_constitution = self._config_value("personality", "constitution")
        if isinstance(raw_constitution, list):
            lines = [f"- {line}" for line in raw_constitution if isinstance(line, str) and line.strip()]
            if lines:
                return "Constitution:\n" + "\n".join(lines)
        return (
            "Constitution:\n"
            "- Never fabricate information.\n"
            "- Keep private data private.\n"
            "- Require approval for state-changing actions."
        )

    def _tool_descriptions_content(self) -> str:
        skill_registry = self._turn_context().skill_registry
        if skill_registry is None:
            return "Tool descriptions: no registered skills."

        descriptions = [
            f"- {skill.name}: {skill.description}"
            for skill in skill_registry.list_all()
            if skill.description.strip()
        ]
        if not descriptions:
            return "Tool descriptions: no registered skills."
        return "Tool descriptions:\n" + "\n".join(descriptions)

    def _configuration_content(self) -> str:
        config = self._turn_context().config
        if config is None:
            return "Runtime configuration snapshot: <missing>"

        payload: object
        model_dump = getattr(config, "model_dump", None)
        if callable(model_dump):
            payload = model_dump(mode="json")
        elif isinstance(config, dict):
            payload = config
        else:
            payload = getattr(config, "__dict__", str(config))

        serialized = json.dumps(payload, sort_keys=True, default=str)
        return f"Runtime configuration snapshot:\n{serialized}"

    def _file_subscription_targets(self, item: WorkItem) -> tuple[str, ...]:
        targets: list[str] = []
        for raw_target in item.input_artifacts_from:
            if not isinstance(raw_target, str):
                continue
            target = raw_target.strip()
            if not target or target in targets:
                continue
            targets.append(target)
        return tuple(targets)

    def _has_active_goal_connection_dependencies(self) -> bool:
        active_goal = self._config_value("active_goal")
        if not isinstance(active_goal, str) or not active_goal.strip():
            return False

        raw_dependencies = self._config_value("active_goal_connection_dependencies")
        if isinstance(raw_dependencies, bool):
            return raw_dependencies
        if isinstance(raw_dependencies, (list, tuple, set)):
            return bool(raw_dependencies)
        return True

    @staticmethod
    def _is_cron_schedule(schedule: str | None) -> bool:
        if not isinstance(schedule, str):
            return False
        parts = schedule.split()
        return len(parts) == 5

    def _prepend_high_confidence_suggestions(self, response_text: str, suggestions: list[SuggestionProposal]) -> str:
        if not suggestions:
            return response_text
        preface = "\n".join(f"Suggestion: {suggestion.text}" for suggestion in suggestions)
        return f"{preface}\n\n{response_text}" if response_text else preface

    async def _audit(self, event: str, **data: object) -> None:
        audit_log = self._turn_context().audit
        if audit_log is None:
            return
        await audit_log.log(event, **data)

    def _get_context_manager(self) -> LiveContextManager | None:
        if self.context_manager is not None:
            return self.context_manager
        return self._turn_context().context_manager

    def _get_suggestion_engine(self) -> SuggestionEngine | None:
        if self.suggestion_engine is not None:
            return self.suggestion_engine
        return self._turn_context().suggestion_engine

    def _get_autonomy_calibrator(self) -> AutonomyCalibrator | None:
        if self.autonomy_calibrator is not None:
            return self.autonomy_calibrator
        return self._turn_context().autonomy_calibrator

    def _ensure_session_id(self) -> str:
        active_session_id = self._active_session_id.get()
        if active_session_id is not None:
            return active_session_id
        if self.session_id is None:
            self.session_id = str(uuid.uuid4())
        return self.session_id
