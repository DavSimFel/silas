"""Live ContextManager implementation with Tier 2 scorer eviction."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from silas.agents.structured import run_structured_agent
from silas.core.token_counter import HeuristicTokenCounter
from silas.models.context import (
    ContextItem,
    ContextProfile,
    ContextSubscription,
    ContextZone,
    TokenBudget,
)
from silas.models.scorer import ScorerOutput

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
_ZONE_ORDER: dict[ContextZone, int] = {
    ContextZone.system: 0,
    ContextZone.chronicle: 1,
    ContextZone.memory: 2,
    ContextZone.workspace: 3,
}
_FALLBACK_PROFILE = ContextProfile(
    name="conversation",
    chronicle_pct=0.45,
    memory_pct=0.20,
    workspace_pct=0.15,
)
_SCORER_TIMEOUT_SECONDS = 2.0
_SCORER_BREAKER_FAILURE_LIMIT = 3
_SCORER_BREAKER_COOLDOWN = timedelta(minutes=5)


class _ScorerRunnable(Protocol):
    async def run(self, prompt: str) -> object: ...


@dataclass(slots=True)
class _ScorerCallResult:
    output: ScorerOutput | None = None
    error: Exception | None = None


class LiveContextManager:
    def __init__(
        self,
        token_budget: TokenBudget,
        token_counter: HeuristicTokenCounter,
        scorer_agent: _ScorerRunnable | None = None,
        scorer_timeout_seconds: float = _SCORER_TIMEOUT_SECONDS,
    ):
        self._token_budget = token_budget
        self._token_counter = token_counter
        self._scorer_agent = scorer_agent
        self._scorer_timeout_seconds = scorer_timeout_seconds
        self._scorer_consecutive_failures = 0
        self._scorer_breaker_open_until: datetime | None = None

        # Context storage by scope.
        self.by_scope: dict[str, list[ContextItem]] = {}
        self.subscriptions_by_scope: dict[str, dict[str, ContextSubscription]] = {}
        self.profile_by_scope: dict[str, str] = {}

    def set_scorer(self, scorer_agent: _ScorerRunnable | None) -> None:
        self._scorer_agent = scorer_agent
        self._scorer_consecutive_failures = 0
        self._scorer_breaker_open_until = None

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
        self._apply_observation_masking(scope_id, turn_number)

        evicted: list[str] = []
        self._extend_unique(evicted, self._evict_to_zone_budgets(scope_id))
        if not self._is_over_threshold(scope_id, self._token_budget.eviction_threshold_pct):
            return evicted

        self._extend_unique(evicted, self._evict_with_scorer(scope_id, turn_number, current_goal))
        if self._is_over_threshold(scope_id, self._token_budget.eviction_threshold_pct):
            self._extend_unique(evicted, self._aggressive_heuristic_eviction(scope_id))

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

    def _evict_to_zone_budgets(self, scope_id: str) -> list[str]:
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

    def _evict_with_scorer(
        self,
        scope_id: str,
        turn_number: int,
        current_goal: str | None,
    ) -> list[str]:
        if self._scorer_agent is None:
            return []
        if not self._scorer_circuit_closed():
            return []

        scorer_output = self._run_scorer(scope_id, turn_number, current_goal)
        if scorer_output is None:
            self._record_scorer_failure()
            return []

        self._record_scorer_success()
        return self._evict_from_scorer_groups(scope_id, scorer_output)

    def _run_scorer(
        self,
        scope_id: str,
        turn_number: int,
        current_goal: str | None,
    ) -> ScorerOutput | None:
        if self._scorer_agent is None:
            return None

        prompt = self._build_scorer_prompt(scope_id, turn_number, current_goal)
        call_result = _ScorerCallResult()

        def _invoke() -> None:
            try:
                result = asyncio.run(
                    run_structured_agent(
                        agent=self._scorer_agent,
                        prompt=prompt,
                        call_name="scorer",
                        default_context_profile=self._token_budget.default_profile,
                    )
                )
            except Exception as err:
                call_result.error = err
                return

            if isinstance(result, ScorerOutput):
                call_result.output = result
                return
            call_result.error = TypeError("scorer must return ScorerOutput")

        worker = threading.Thread(target=_invoke, daemon=True)
        worker.start()
        worker.join(timeout=self._scorer_timeout_seconds)

        if worker.is_alive():
            return None
        if call_result.error is not None:
            return None
        return call_result.output

    def _build_scorer_prompt(
        self,
        scope_id: str,
        turn_number: int,
        current_goal: str | None,
    ) -> str:
        goal = current_goal or "(none)"
        recent_turns = self._render_recent_turns(scope_id, turn_number)
        blocks = self._render_scorer_blocks(scope_id)

        return (
            "You are a context relevance scorer. Given the current conversation goal\n"
            "and recent turns, identify which context groups are least valuable.\n\n"
            f"Current goal: {goal}\n"
            f"Recent turns (last 2-3):\n{recent_turns}\n\n"
            "Context blocks to evaluate:\n"
            f"{blocks}\n\n"
            'Output two lists: "keep_groups" and "evict_groups".\n'
            "Group related blocks together. Prefer coherent group eviction over orphaning blocks."
        )

    def _render_recent_turns(self, scope_id: str, turn_number: int) -> str:
        chronicle = [
            item
            for item in self.by_scope.get(scope_id, [])
            if item.zone == ContextZone.chronicle and item.turn_number <= turn_number
        ]
        chronicle.sort(key=lambda item: (item.turn_number, item.created_at, item.ctx_id))
        if not chronicle:
            return "- (none)"

        lines: list[str] = []
        for item in chronicle[-3:]:
            lines.append(
                f"- turn {item.turn_number} | {item.source} | "
                f"{self._truncate_text(item.content, max_chars=180)}"
            )
        return "\n".join(lines)

    def _render_scorer_blocks(self, scope_id: str) -> str:
        items = list(self.by_scope.get(scope_id, []))
        if not items:
            return "- (none)"

        items.sort(
            key=lambda item: (
                _ZONE_ORDER[item.zone],
                item.turn_number,
                item.created_at,
                item.ctx_id,
            )
        )

        lines: list[str] = []
        for item in items:
            lines.append(
                f"- id={item.ctx_id} | zone={item.zone.value} | kind={item.kind} | "
                f"turn={item.turn_number} | source={item.source} | tokens={item.token_count} | "
                f"relevance={item.relevance:.3f} | pinned={item.pinned}"
            )
            lines.append(f"  content={self._truncate_text(item.content, max_chars=200)}")
        return "\n".join(lines)

    def _evict_from_scorer_groups(self, scope_id: str, scorer_output: ScorerOutput) -> list[str]:
        evicted: list[str] = []
        seen_block_ids: set[str] = set()

        for group in scorer_output.evict_groups:
            for block_id in group.block_ids:
                if block_id in seen_block_ids:
                    continue
                seen_block_ids.add(block_id)

                item = self._find_item(scope_id, block_id)
                if item is None or item.pinned or item.zone == ContextZone.system:
                    continue

                removed = self._remove_item(scope_id, block_id)
                if removed is not None:
                    evicted.append(removed.ctx_id)

            if not self._is_over_threshold(scope_id, self._token_budget.eviction_threshold_pct):
                break

        return evicted

    def _aggressive_heuristic_eviction(self, scope_id: str) -> list[str]:
        evicted: list[str] = []
        while self._is_over_threshold(scope_id, self._token_budget.eviction_threshold_pct):
            candidate = self._pick_aggressive_candidate(scope_id)
            if candidate is None:
                break

            removed = self._remove_item(scope_id, candidate.ctx_id)
            if removed is None:
                break
            evicted.append(removed.ctx_id)

        return evicted

    def _pick_eviction_candidate(self, scope_id: str, zone: ContextZone) -> ContextItem | None:
        candidates = [
            item for item in self.by_scope.get(scope_id, []) if item.zone == zone and not item.pinned
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

    def _pick_aggressive_candidate(self, scope_id: str) -> ContextItem | None:
        candidates = [
            item
            for item in self.by_scope.get(scope_id, [])
            if item.zone in _EVICTABLE_ZONES and not item.pinned
        ]
        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (
                0 if item.zone == ContextZone.chronicle else 1 if item.zone == ContextZone.memory else 2,
                item.turn_number if item.zone == ContextZone.chronicle else int(item.relevance * 1_000_000),
                item.turn_number,
                item.created_at,
                item.ctx_id,
            )
        )
        return candidates[0]

    def _scorer_circuit_closed(self) -> bool:
        if self._scorer_breaker_open_until is None:
            return True

        now = datetime.now(timezone.utc)
        if now >= self._scorer_breaker_open_until:
            self._scorer_consecutive_failures = 0
            self._scorer_breaker_open_until = None
            return True
        return False

    def _record_scorer_success(self) -> None:
        self._scorer_consecutive_failures = 0
        self._scorer_breaker_open_until = None

    def _record_scorer_failure(self) -> None:
        self._scorer_consecutive_failures += 1
        if self._scorer_consecutive_failures < _SCORER_BREAKER_FAILURE_LIMIT:
            return
        self._scorer_breaker_open_until = datetime.now(timezone.utc) + _SCORER_BREAKER_COOLDOWN

    def _find_item(self, scope_id: str, ctx_id: str) -> ContextItem | None:
        for item in self.by_scope.get(scope_id, []):
            if item.ctx_id == ctx_id:
                return item
        return None

    def _total_usage_tokens(self, scope_id: str) -> int:
        usage = self.token_usage(scope_id)
        return sum(usage.values())

    def _is_over_threshold(self, scope_id: str, threshold_pct: float) -> bool:
        threshold_tokens = int(self._token_budget.total * threshold_pct)
        return self._total_usage_tokens(scope_id) > threshold_tokens

    def _truncate_text(self, text: str, max_chars: int = 200) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_chars:
            return compact
        if max_chars <= 3:
            return compact[:max_chars]
        return f"{compact[:max_chars - 3]}..."

    def _extend_unique(self, target: list[str], additions: list[str]) -> None:
        seen = set(target)
        for ctx_id in additions:
            if ctx_id in seen:
                continue
            seen.add(ctx_id)
            target.append(ctx_id)

    def _remove_item(self, scope_id: str, ctx_id: str) -> ContextItem | None:
        items = self.by_scope.get(scope_id)
        if items is None:
            return None

        for idx, item in enumerate(items):
            if item.ctx_id == ctx_id:
                return items.pop(idx)
        return None


__all__ = ["LiveContextManager"]
