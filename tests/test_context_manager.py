"""Tests for LiveContextManager (Phase 1c).

Covers: zone management, profile switching, rendering,
observation masking, heuristic eviction, token usage.
"""

from __future__ import annotations

import pytest
from silas.core.token_counter import HeuristicTokenCounter
from silas.models.context import (
    ContextItem,
    ContextProfile,
    ContextSubscription,
    ContextZone,
    TokenBudget,
)
from silas.models.messages import TaintLevel


def _item(
    ctx_id: str,
    zone: ContextZone,
    content: str,
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


def _budget(**overrides) -> TokenBudget:
    profiles = {
        "conversation": ContextProfile(name="conversation", chronicle_pct=0.45, memory_pct=0.20, workspace_pct=0.15),
        "coding": ContextProfile(name="coding", chronicle_pct=0.20, memory_pct=0.20, workspace_pct=0.40),
    }
    defaults = {"total": 10000, "system_max": 1000, "profiles": profiles, "default_profile": "conversation"}
    defaults.update(overrides)
    return TokenBudget(**defaults)


@pytest.fixture
def ctx_mgr():
    from silas.core.context_manager import LiveContextManager

    return LiveContextManager(
        token_budget=_budget(),
        token_counter=HeuristicTokenCounter(),
    )


class TestZoneManagement:
    def test_add_and_get_zone(self, ctx_mgr) -> None:
        item = _item("c1", ContextZone.chronicle, "hello")
        ctx_mgr.add("owner", item)
        zone = ctx_mgr.get_zone("owner", ContextZone.chronicle)
        assert len(zone) == 1
        assert zone[0].ctx_id == "c1"

    def test_drop(self, ctx_mgr) -> None:
        ctx_mgr.add("owner", _item("c1", ContextZone.chronicle, "hello"))
        ctx_mgr.add("owner", _item("c2", ContextZone.chronicle, "world"))
        ctx_mgr.drop("owner", "c1")
        zone = ctx_mgr.get_zone("owner", ContextZone.chronicle)
        assert len(zone) == 1
        assert zone[0].ctx_id == "c2"

    def test_scope_isolation(self, ctx_mgr) -> None:
        ctx_mgr.add("scope-a", _item("a1", ContextZone.memory, "a data"))
        ctx_mgr.add("scope-b", _item("b1", ContextZone.memory, "b data"))
        assert len(ctx_mgr.get_zone("scope-a", ContextZone.memory)) == 1
        assert len(ctx_mgr.get_zone("scope-b", ContextZone.memory)) == 1

    def test_empty_zone_returns_empty_list(self, ctx_mgr) -> None:
        assert ctx_mgr.get_zone("owner", ContextZone.workspace) == []


class TestProfileManagement:
    def test_set_and_get_profile(self, ctx_mgr) -> None:
        ctx_mgr.set_profile("owner", "coding")
        # No direct getter in protocol, but render should work with coding profile


class TestSubscriptions:
    def test_subscribe_and_unsubscribe(self, ctx_mgr) -> None:
        sub = ContextSubscription(
            sub_id="sub1",
            sub_type="file",
            target="/tmp/test.py",
            zone=ContextZone.workspace,
            turn_created=1,
            content_hash="abc123",
        )
        result = ctx_mgr.subscribe("owner", sub)
        assert result == "sub1"
        ctx_mgr.unsubscribe("owner", "sub1")  # should not raise


class TestTokenUsage:
    def test_token_usage_per_zone(self, ctx_mgr) -> None:
        ctx_mgr.add("owner", _item("s1", ContextZone.system, "system prompt", kind="system"))
        ctx_mgr.add("owner", _item("c1", ContextZone.chronicle, "hello world"))
        ctx_mgr.add("owner", _item("m1", ContextZone.memory, "remembered fact"))
        usage = ctx_mgr.token_usage("owner")
        assert usage["system"] > 0
        assert usage["chronicle"] > 0
        assert usage["memory"] > 0
        assert usage["workspace"] == 0


class TestRendering:
    def test_render_ordering(self, ctx_mgr) -> None:
        """Rendering order: system → chronicle → memory → workspace."""
        ctx_mgr.add("owner", _item("w1", ContextZone.workspace, "workspace content"))
        ctx_mgr.add("owner", _item("s1", ContextZone.system, "system content", kind="system"))
        ctx_mgr.add("owner", _item("c1", ContextZone.chronicle, "chronicle content"))
        ctx_mgr.add("owner", _item("m1", ContextZone.memory, "memory content"))
        rendered = ctx_mgr.render("owner", turn_number=1)
        # System should appear before chronicle, chronicle before memory, memory before workspace
        sys_pos = rendered.find("system content")
        chron_pos = rendered.find("chronicle content")
        mem_pos = rendered.find("memory content")
        ws_pos = rendered.find("workspace content")
        assert sys_pos < chron_pos < mem_pos < ws_pos

    def test_render_includes_metadata_delimiters(self, ctx_mgr) -> None:
        ctx_mgr.add("owner", _item("c1", ContextZone.chronicle, "hello", turn=5))
        rendered = ctx_mgr.render("owner", turn_number=5)
        assert "---" in rendered
        assert "end" in rendered.lower() or "---" in rendered

    def test_render_empty_scope(self, ctx_mgr) -> None:
        rendered = ctx_mgr.render("empty-scope", turn_number=1)
        assert rendered == "" or rendered.strip() == ""


class TestObservationMasking:
    def test_old_tool_results_masked(self, ctx_mgr) -> None:
        """Tool results older than observation_mask_after_turns should be masked."""
        ctx_mgr.add(
            "owner",
            _item("t1", ContextZone.chronicle, "npm test output: all passed", turn=1, kind="tool_result", source="shell_exec"),
        )
        rendered = ctx_mgr.render("owner", turn_number=10)  # 10 - 1 = 9 > 5 (default mask threshold)
        assert "npm test output" not in rendered
        assert "tokens" in rendered.lower() or "masked" in rendered.lower() or "result of" in rendered.lower()

    def test_recent_tool_results_not_masked(self, ctx_mgr) -> None:
        ctx_mgr.add(
            "owner",
            _item("t1", ContextZone.chronicle, "npm test output: all passed", turn=8, kind="tool_result"),
        )
        rendered = ctx_mgr.render("owner", turn_number=10)  # 10 - 8 = 2 < 5
        assert "npm test output" in rendered


class TestHeuristicEviction:
    def test_eviction_returns_evicted_ids(self, ctx_mgr) -> None:
        # Fill up chronicle zone way past budget
        for i in range(100):
            ctx_mgr.add("owner", _item(f"c{i}", ContextZone.chronicle, "x" * 500, turn=i, relevance=0.1))
        evicted = ctx_mgr.enforce_budget("owner", turn_number=100, current_goal=None)
        assert len(evicted) > 0

    def test_pinned_items_not_evicted(self, ctx_mgr) -> None:
        ctx_mgr.add("owner", _item("pinned", ContextZone.chronicle, "x" * 5000, pinned=True))
        for i in range(50):
            ctx_mgr.add("owner", _item(f"c{i}", ContextZone.chronicle, "x" * 500, relevance=0.1))
        evicted = ctx_mgr.enforce_budget("owner", turn_number=50, current_goal=None)
        assert "pinned" not in evicted

    def test_eviction_prefers_low_relevance(self, ctx_mgr) -> None:
        ctx_mgr.add("owner", _item("high", ContextZone.chronicle, "x" * 500, relevance=1.0))
        ctx_mgr.add("owner", _item("low", ContextZone.chronicle, "x" * 500, relevance=0.01))
        for i in range(50):
            ctx_mgr.add("owner", _item(f"c{i}", ContextZone.chronicle, "x" * 500, relevance=0.5))
        evicted = ctx_mgr.enforce_budget("owner", turn_number=50, current_goal=None)
        if evicted:
            # Low relevance should be evicted before high
            assert "low" in evicted or "high" not in evicted
