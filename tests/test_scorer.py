from __future__ import annotations

import pytest
from silas.agents.scorer import score_context_blocks
from silas.core.context_manager import LiveContextManager
from silas.core.token_counter import HeuristicTokenCounter
from silas.models.context import (
    ContextItem,
    ContextProfile,
    ContextZone,
    ScorerGroup,
    ScorerOutput,
    TokenBudget,
)
from silas.models.messages import TaintLevel

from tests.fakes import FakeContextScorer


def _item(
    ctx_id: str,
    zone: ContextZone,
    content: str,
    *,
    turn: int = 1,
    kind: str = "message",
    relevance: float = 1.0,
    source: str = "test",
    pinned: bool = False,
) -> ContextItem:
    counter = HeuristicTokenCounter()
    return ContextItem(
        ctx_id=ctx_id,
        zone=zone,
        content=content,
        token_count=counter.count(content),
        turn_number=turn,
        source=source,
        taint=TaintLevel.owner,
        kind=kind,
        relevance=relevance,
        pinned=pinned,
    )


def _budget(
    *,
    total: int = 1000,
    system_max: int = 100,
    eviction_threshold_pct: float = 0.50,
    profiles: dict[str, ContextProfile] | None = None,
) -> TokenBudget:
    configured_profiles = profiles or {
        "conversation": ContextProfile(
            name="conversation",
            chronicle_pct=0.45,
            memory_pct=0.20,
            workspace_pct=0.15,
        )
    }
    return TokenBudget(
        total=total,
        system_max=system_max,
        eviction_threshold_pct=eviction_threshold_pct,
        profiles=configured_profiles,
        default_profile="conversation",
    )


def _manager(
    budget: TokenBudget,
    scorer: FakeContextScorer | None = None,
    *,
    timeout_seconds: float = 2.0,
) -> LiveContextManager:
    return LiveContextManager(
        token_budget=budget,
        token_counter=HeuristicTokenCounter(),
        scorer_agent=scorer,
        scorer_timeout_seconds=timeout_seconds,
    )


def _seed_over_budget(manager: LiveContextManager, scope_id: str) -> list[str]:
    block_ids: list[str] = []
    for idx in range(4):
        block_id = f"c{idx}"
        manager.add(
            scope_id,
            _item(block_id, ContextZone.chronicle, "x" * 350, turn=idx + 1, relevance=0.6),
        )
        block_ids.append(block_id)
    for idx in range(4):
        block_id = f"m{idx}"
        manager.add(
            scope_id,
            _item(block_id, ContextZone.memory, "y" * 350, turn=idx + 1, relevance=0.2),
        )
        block_ids.append(block_id)
    return block_ids


@pytest.mark.asyncio
async def test_scorer_output_parsing_via_structured_wrapper() -> None:
    expected = ScorerOutput(
        keep_groups=[ScorerGroup(reason="retain active thread", block_ids=["c1", "c2"])],
        evict_groups=[ScorerGroup(reason="stale branch", block_ids=["m9"])],
    )
    scorer = FakeContextScorer(outputs=[expected.model_copy(deep=True)])

    parsed = await score_context_blocks(scorer, "score these blocks")

    assert parsed.model_dump(mode="json") == expected.model_dump(mode="json")
    assert scorer.calls == 1


def test_scorer_output_model_parses_groups() -> None:
    payload = {
        "keep_groups": [{"reason": "recent", "block_ids": ["a", "b"]}],
        "evict_groups": [{"reason": "stale", "block_ids": ["c"]}],
    }

    parsed = ScorerOutput.model_validate(payload)

    assert parsed.keep_groups[0].reason == "recent"
    assert parsed.keep_groups[0].block_ids == ["a", "b"]
    assert parsed.evict_groups[0].block_ids == ["c"]


def test_timeout_falls_back_to_aggressive_heuristic_eviction() -> None:
    scorer = FakeContextScorer(delay_seconds=0.20)
    manager = _manager(_budget(), scorer=scorer, timeout_seconds=0.01)
    _seed_over_budget(manager, "owner")

    evicted = manager.enforce_budget("owner", turn_number=10, current_goal="keep recent debugging state")

    assert scorer.calls == 1
    assert manager._scorer_consecutive_failures == 1  # noqa: SLF001
    assert len(evicted) >= 3
    total_tokens = sum(manager.token_usage("owner").values())
    assert total_tokens <= 500


def test_scorer_circuit_breaker_opens_after_three_failures() -> None:
    scorer = FakeContextScorer(errors=[RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])
    manager = _manager(_budget(), scorer=scorer, timeout_seconds=0.05)

    for idx in range(4):
        scope = f"scope-{idx}"
        _seed_over_budget(manager, scope)
        manager.enforce_budget(scope, turn_number=10, current_goal="triage")

    assert scorer.calls == 3
    assert manager._scorer_breaker_open_until is not None  # noqa: SLF001


def test_context_manager_uses_scorer_groups_for_eviction() -> None:
    profiles = {
        "conversation": ContextProfile(
            name="conversation",
            chronicle_pct=0.40,
            memory_pct=0.40,
            workspace_pct=0.00,
        )
    }
    budget = _budget(profiles=profiles)
    scorer = FakeContextScorer(
        outputs=[
            ScorerOutput(
                keep_groups=[ScorerGroup(reason="keep latest chronicle", block_ids=["c0", "c1", "c2"])],
                evict_groups=[ScorerGroup(reason="drop memory shard", block_ids=["m0"])],
            )
        ]
    )
    manager = _manager(budget, scorer=scorer)

    for idx in range(3):
        manager.add("owner", _item(f"c{idx}", ContextZone.chronicle, "a" * 350, turn=idx + 1))
        manager.add("owner", _item(f"m{idx}", ContextZone.memory, "b" * 350, turn=idx + 1))

    evicted = manager.enforce_budget("owner", turn_number=9, current_goal="focus on live thread")

    assert scorer.calls == 1
    assert "m0" in evicted
    remaining_ids = {item.ctx_id for item in manager.by_scope["owner"]}
    assert "m0" not in remaining_ids
    assert {"c0", "c1", "c2"}.issubset(remaining_ids)


def test_empty_context_is_noop() -> None:
    scorer = FakeContextScorer(outputs=[ScorerOutput()])
    manager = _manager(_budget(), scorer=scorer)

    evicted = manager.enforce_budget("empty", turn_number=1, current_goal=None)

    assert evicted == []
    assert scorer.calls == 0


def test_all_high_priority_pinned_items_are_preserved() -> None:
    scorer = FakeContextScorer(
        outputs=[
            ScorerOutput(
                evict_groups=[ScorerGroup(reason="attempt", block_ids=["c0", "m0"])],
            )
        ]
    )
    manager = _manager(_budget(), scorer=scorer)
    manager.add("owner", _item("c0", ContextZone.chronicle, "c" * 350, pinned=True))
    manager.add("owner", _item("m0", ContextZone.memory, "m" * 350, pinned=True))

    evicted = manager.enforce_budget("owner", turn_number=7, current_goal="none")

    assert evicted == []
    assert {item.ctx_id for item in manager.by_scope["owner"]} == {"c0", "m0"}


def test_all_low_priority_items_can_be_evicted() -> None:
    profiles = {
        "conversation": ContextProfile(
            name="conversation",
            chronicle_pct=0.40,
            memory_pct=0.40,
            workspace_pct=0.00,
        )
    }
    budget = _budget(total=500, eviction_threshold_pct=0.40, profiles=profiles)
    scorer = FakeContextScorer(
        outputs=[
            ScorerOutput(
                evict_groups=[
                    ScorerGroup(reason="drop stale data", block_ids=["c0", "c1", "m0", "m1"])
                ]
            )
        ]
    )
    manager = _manager(budget, scorer=scorer)

    manager.add("owner", _item("c0", ContextZone.chronicle, "c" * 350, relevance=0.01))
    manager.add("owner", _item("c1", ContextZone.chronicle, "c" * 350, relevance=0.01))
    manager.add("owner", _item("m0", ContextZone.memory, "m" * 350, relevance=0.01))
    manager.add("owner", _item("m1", ContextZone.memory, "m" * 350, relevance=0.01))

    evicted = manager.enforce_budget("owner", turn_number=12, current_goal="latest only")

    assert set(evicted) == {"c0", "c1", "m0", "m1"}
    assert manager.by_scope["owner"] == []
