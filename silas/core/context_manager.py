"""Live ContextManager implementation (Phase 1c)."""

from __future__ import annotations

from datetime import datetime, timezone

from silas.core.token_counter import HeuristicTokenCounter
from silas.models.context import (
    ContextItem,
    ContextProfile,
    ContextSubscription,
    ContextZone,
    TokenBudget,
)

_RENDER_ORDER: tuple[ContextZone, ...] = (
    ContextZone.system,
    ContextZone.chronicle,
    ContextZone.memory,
    ContextZone.workspace,
)
_EVICTABLE_ZONES: tuple[ContextZone, ...] = (
    ContextZone.chronicle,
    ContextZone.memory,
    ContextZone.workspace,
)
_FALLBACK_PROFILE = ContextProfile(
    name="conversation",
    chronicle_pct=0.45,
    memory_pct=0.20,
    workspace_pct=0.15,
)


class LiveContextManager:
    def __init__(self, token_budget: TokenBudget, token_counter: HeuristicTokenCounter):
        self._token_budget = token_budget
        self._token_counter = token_counter

        # Context storage by scope.
        self.by_scope: dict[str, list[ContextItem]] = {}
        self.subscriptions_by_scope: dict[str, dict[str, ContextSubscription]] = {}
        self.profile_by_scope: dict[str, str] = {}

    def add(self, scope_id: str, item: ContextItem) -> str:
        stored_item = item.model_copy(deep=True)
        self.by_scope.setdefault(scope_id, []).append(stored_item)
        return stored_item.ctx_id

    def drop(self, scope_id: str, ctx_id: str) -> None:
        self._remove_item(scope_id, ctx_id)

    def get_zone(self, scope_id: str, zone: ContextZone) -> list[ContextItem]:
        return [item for item in self.by_scope.get(scope_id, []) if item.zone == zone]

    def subscribe(self, scope_id: str, sub: ContextSubscription) -> str:
        stored_sub = sub.model_copy(update={"created_at": datetime.now(timezone.utc)})
        self.subscriptions_by_scope.setdefault(scope_id, {})[stored_sub.sub_id] = stored_sub
        return stored_sub.sub_id

    def unsubscribe(self, scope_id: str, sub_id: str) -> None:
        subscriptions = self.subscriptions_by_scope.get(scope_id)
        if subscriptions is None:
            return

        sub = subscriptions.get(sub_id)
        if sub is None:
            return

        subscriptions[sub_id] = sub.model_copy(update={"active": False, "token_count": 0})

    def set_profile(self, scope_id: str, profile_name: str) -> None:
        if self._token_budget.profiles and profile_name not in self._token_budget.profiles:
            raise ValueError(f"unknown context profile: {profile_name}")
        self.profile_by_scope[scope_id] = profile_name

    def render(self, scope_id: str, turn_number: int) -> str:
        self._apply_observation_masking(scope_id, turn_number)

        scope_items = self.by_scope.get(scope_id, [])
        if not scope_items:
            return ""

        blocks: list[str] = []
        for zone in _RENDER_ORDER:
            zone_items = [item for item in scope_items if item.zone == zone]
            for item in zone_items:
                blocks.append(
                    f"--- {zone.value} | turn {item.turn_number} | {item.source} ---\n"
                    f"{item.content}\n"
                    "--- end ---"
                )
        return "\n\n".join(blocks)

    def enforce_budget(self, scope_id: str, turn_number: int, current_goal: str | None) -> list[str]:
        del current_goal  # Phase 1c: heuristic-only eviction (no scorer context).
        self._apply_observation_masking(scope_id, turn_number)

        usage = self.token_usage(scope_id)
        budgets = self._zone_budgets(scope_id, usage[ContextZone.system.value])
        evicted: list[str] = []

        for zone in _EVICTABLE_ZONES:
            zone_key = zone.value
            zone_budget = budgets[zone_key]
            while usage[zone_key] > zone_budget:
                candidate = self._pick_eviction_candidate(scope_id, zone)
                if candidate is None:
                    break

                removed = self._remove_item(scope_id, candidate.ctx_id)
                if removed is None:
                    break

                evicted.append(removed.ctx_id)
                usage[zone_key] -= removed.token_count

        return evicted

    def token_usage(self, scope_id: str) -> dict[str, int]:
        usage = {zone.value: 0 for zone in ContextZone}
        for item in self.by_scope.get(scope_id, []):
            usage[item.zone.value] += item.token_count
        return usage

    def _zone_budgets(self, scope_id: str, system_zone_tokens: int) -> dict[str, int]:
        profile = self._get_profile(scope_id)
        return {
            ContextZone.system.value: self._token_budget.system_max,
            ContextZone.chronicle.value: self._token_budget.zone_budget(
                ContextZone.chronicle,
                profile,
                system_zone_tokens,
            ),
            ContextZone.memory.value: self._token_budget.zone_budget(
                ContextZone.memory,
                profile,
                system_zone_tokens,
            ),
            ContextZone.workspace.value: self._token_budget.zone_budget(
                ContextZone.workspace,
                profile,
                system_zone_tokens,
            ),
        }

    def _get_profile(self, scope_id: str) -> ContextProfile:
        profile_name = self.profile_by_scope.get(scope_id, self._token_budget.default_profile)

        if not self._token_budget.profiles:
            return _FALLBACK_PROFILE.model_copy(update={"name": profile_name})

        profile = self._token_budget.profiles.get(profile_name)
        if profile is not None:
            return profile

        default_profile = self._token_budget.profiles[self._token_budget.default_profile]
        self.profile_by_scope[scope_id] = default_profile.name
        return default_profile

    def _apply_observation_masking(self, scope_id: str, turn_number: int) -> None:
        threshold = self._token_budget.observation_mask_after_turns
        for item in self.by_scope.get(scope_id, []):
            if item.kind != "tool_result" or item.masked:
                continue
            if turn_number - item.turn_number <= threshold:
                continue

            original_tokens = item.token_count
            placeholder = (
                f"[Result of {item.source} — {original_tokens} tokens — see memory for details]"
            )
            item.content = placeholder
            item.masked = True
            item.token_count = self._token_counter.count(placeholder)

    def _pick_eviction_candidate(self, scope_id: str, zone: ContextZone) -> ContextItem | None:
        candidates = [
            item
            for item in self.by_scope.get(scope_id, [])
            if item.zone == zone and not item.pinned
        ]
        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (
                item.relevance,
                item.turn_number,
                item.created_at,
                item.ctx_id,
            )
        )
        return candidates[0]

    def _remove_item(self, scope_id: str, ctx_id: str) -> ContextItem | None:
        items = self.by_scope.get(scope_id)
        if items is None:
            return None

        for idx, item in enumerate(items):
            if item.ctx_id == ctx_id:
                return items.pop(idx)
        return None


__all__ = ["LiveContextManager"]
