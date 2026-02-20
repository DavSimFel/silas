"""Live ContextManager implementation (Phase 1c + tier-2 scorer)."""

from __future__ import annotations

from datetime import UTC, datetime

from silas.context.registry import ContextRegistry
from silas.context.scorer import ContextScorer
from silas.core.token_counter import HeuristicTokenCounter
from silas.models.context import (
    ContextItem,
    ContextProfile,
    ContextSubscription,
    ContextZone,
    TokenBudget,
)
from silas.models.context_item import ContextItem as UnifiedContextItem

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
    def __init__(
        self,
        token_budget: TokenBudget,
        token_counter: HeuristicTokenCounter,
        *,
        use_scorer: bool = True,
        scorer: ContextScorer | None = None,
    ):
        self._token_budget = token_budget
        self._token_counter = token_counter
        self._use_scorer = use_scorer
        self._scorer = scorer or ContextScorer()

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
        stored_sub = sub.model_copy(update={"created_at": datetime.now(UTC)})
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

    def enforce_budget(
        self, scope_id: str, turn_number: int, current_goal: str | None
    ) -> list[str]:
        self._apply_observation_masking(scope_id, turn_number)

        usage = self.token_usage(scope_id)
        budgets = self._zone_budgets(scope_id, usage[ContextZone.system.value])
        evicted: list[str] = []

        for zone in _EVICTABLE_ZONES:
            zone_key = zone.value
            zone_budget = budgets[zone_key]

            if usage[zone_key] <= zone_budget:
                continue

            # Tier-2: rank candidates by relevance score so we evict the
            # least-valuable items first, not just the oldest.
            if self._use_scorer:
                evicted += self._evict_scored(
                    scope_id,
                    zone,
                    zone_budget,
                    usage,
                    current_goal or "",
                )
            else:
                # Tier-1 fallback: pure heuristic (FIFO + relevance).
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

    def _evict_scored(
        self,
        scope_id: str,
        zone: ContextZone,
        zone_budget: int,
        usage: dict[str, int],
        current_query: str,
    ) -> list[str]:
        """Evict lowest-scored items in *zone* until under budget."""
        zone_key = zone.value
        candidates = [
            item
            for item in self.by_scope.get(scope_id, [])
            if item.zone == zone and not item.pinned
        ]
        # Score ascending — worst candidates first for eviction.
        scored = self._scorer.score_items(candidates, current_query)
        scored.sort(key=lambda pair: pair[1])

        evicted: list[str] = []
        for item, _score in scored:
            if usage[zone_key] <= zone_budget:
                break
            removed = self._remove_item(scope_id, item.ctx_id)
            if removed is None:
                continue
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

    # ------------------------------------------------------------------
    # Registry bridge
    # ------------------------------------------------------------------

    def populate_registry(self, registry: ContextRegistry) -> None:
        """Populate a ContextRegistry from current zone-based context."""
        zone_to_tag = {
            ContextZone.system: "personality",
            ContextZone.chronicle: "topic",
            ContextZone.memory: "memory",
            ContextZone.workspace: "file",
        }
        for scope_id, items in self.by_scope.items():
            for item in items:
                source = f"{item.zone.value}:{scope_id}:{item.ctx_id}"
                tag = zone_to_tag.get(item.zone, "")
                unified = UnifiedContextItem(
                    item_id=item.ctx_id,
                    content=item.content,
                    source=source,
                    role="system",
                    last_modified=item.created_at,
                    token_count=item.token_count,
                    taint=item.taint.value if hasattr(item.taint, "value") else str(item.taint),
                    eviction_priority=0.9 if item.pinned else 0.5,
                    source_tag=tag,
                    turn_created=item.turn_number,
                    tags=set(),
                )
                registry.upsert(unified)


__all__ = ["LiveContextManager"]
