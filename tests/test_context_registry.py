"""Comprehensive tests for ContextRegistry and ContextItem."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from silas.core.context_registry import ContextRegistry
from silas.models.context_item import ContextItem


def _make_item(
    item_id: str = "item-1",
    content: str = "hello",
    source: str = "test",
    role: str = "system",
    token_count: int = 10,
    **kwargs,
) -> ContextItem:
    return ContextItem(
        item_id=item_id,
        content=content,
        source=source,
        role=role,
        last_modified=kwargs.pop("last_modified", datetime.now(UTC)),
        token_count=token_count,
        **kwargs,
    )


class TestUpsert:
    def test_creates_new_item(self):
        reg = ContextRegistry()
        reg.upsert(_make_item())
        assert reg.count() == 1

    def test_updates_existing_item(self):
        reg = ContextRegistry()
        reg.upsert(_make_item(content="v1"))
        before = reg.get("item-1")
        assert before is not None
        t_before = before.last_modified

        time.sleep(0.001)
        reg.upsert(_make_item(content="v2"))
        after = reg.get("item-1")
        assert after is not None
        assert after.content == "v2"
        assert after.last_modified > t_before

    def test_duplicate_upsert_no_duplicates(self):
        reg = ContextRegistry()
        reg.upsert(_make_item())
        reg.upsert(_make_item())
        assert reg.count() == 1


class TestRemove:
    def test_remove_existing(self):
        reg = ContextRegistry()
        reg.upsert(_make_item())
        assert reg.remove("item-1") is True
        assert reg.count() == 0

    def test_remove_nonexistent(self):
        reg = ContextRegistry()
        assert reg.remove("nope") is False


class TestTouch:
    def test_updates_last_modified(self):
        reg = ContextRegistry()
        reg.upsert(_make_item())
        t1 = reg.get("item-1").last_modified  # type: ignore[union-attr]
        time.sleep(0.001)
        reg.touch("item-1")
        t2 = reg.get("item-1").last_modified  # type: ignore[union-attr]
        assert t2 > t1

    def test_content_unchanged(self):
        reg = ContextRegistry()
        reg.upsert(_make_item(content="original"))
        reg.touch("item-1")
        assert reg.get("item-1").content == "original"  # type: ignore[union-attr]


class TestQuery:
    def test_by_source_prefix(self):
        reg = ContextRegistry()
        reg.upsert(_make_item("a", source="memory:ep:1"))
        reg.upsert(_make_item("b", source="file:config.yaml"))
        assert len(reg.query(source_prefix="memory:")) == 1

    def test_by_role(self):
        reg = ContextRegistry()
        reg.upsert(_make_item("a", role="system"))
        reg.upsert(_make_item("b", role="user"))
        assert len(reg.query(role="user")) == 1

    def test_by_tags(self):
        reg = ContextRegistry()
        reg.upsert(_make_item("a", tags={"important", "urgent"}))
        reg.upsert(_make_item("b", tags={"important"}))
        assert len(reg.query(tags={"important", "urgent"})) == 1
        assert len(reg.query(tags={"important"})) == 2


class TestRender:
    def test_sorted_by_last_modified_desc(self):
        reg = ContextRegistry()
        now = datetime.now(UTC)
        reg.upsert(_make_item("old", last_modified=now - timedelta(hours=2)))
        reg.upsert(_make_item("new", last_modified=now - timedelta(hours=1)))
        result = reg.render("system", budget_tokens=1000)
        assert result[0].item_id == "new"
        assert result[1].item_id == "old"

    def test_respects_budget_tokens(self):
        reg = ContextRegistry()
        reg.upsert(_make_item("a", token_count=60, eviction_priority=0.8))
        reg.upsert(_make_item("b", token_count=60, eviction_priority=0.2))
        result = reg.render("system", budget_tokens=70)
        assert len(result) == 1
        assert result[0].item_id == "a"

    def test_respects_source_tag_caps(self):
        reg = ContextRegistry()
        reg.upsert(_make_item("a", token_count=30, source_tag="memory"))
        reg.upsert(_make_item("b", token_count=30, source_tag="memory"))
        # cap memory at 25% of 100 = 25 tokens, so only 0 items fit
        result = reg.render("system", budget_tokens=100, budget_caps={"memory": 0.25})
        # 25 tokens cap: neither 30-token item fits
        assert len(result) == 0

    def test_source_tag_cap_allows_partial(self):
        reg = ContextRegistry()
        reg.upsert(_make_item("a", token_count=20, source_tag="memory", eviction_priority=0.8))
        reg.upsert(_make_item("b", token_count=20, source_tag="memory", eviction_priority=0.5))
        # cap at 25% of 100 = 25; only one 20-token item fits
        result = reg.render("system", budget_tokens=100, budget_caps={"memory": 0.25})
        assert len(result) == 1
        assert result[0].item_id == "a"

    def test_preserves_chronological_order_for_messages(self):
        reg = ContextRegistry()
        now = datetime.now(UTC)
        reg.upsert(_make_item("m1", source="message:1", last_modified=now - timedelta(minutes=3)))
        reg.upsert(_make_item("m2", source="message:2", last_modified=now - timedelta(minutes=2)))
        reg.upsert(_make_item("m3", source="message:3", last_modified=now - timedelta(minutes=1)))
        result = reg.render("system", budget_tokens=1000)
        ids = [r.item_id for r in result]
        assert ids == ["m1", "m2", "m3"]

    def test_does_not_mutate_registry(self):
        reg = ContextRegistry()
        reg.upsert(_make_item("a", token_count=50))
        reg.upsert(_make_item("b", token_count=50))
        reg.render("system", budget_tokens=60)
        assert reg.count() == 2


class TestEvict:
    def test_removes_expired_ttl_first(self):
        reg = ContextRegistry()
        old_time = datetime.now(UTC) - timedelta(hours=1)
        reg.upsert(_make_item("expired", token_count=50, ttl_seconds=60))
        # Force last_modified to old time so TTL is expired
        reg.get("expired").last_modified = old_time  # type: ignore[union-attr]
        reg.upsert(_make_item("fresh", token_count=50, eviction_priority=0.1))
        evicted = reg.evict(budget_tokens=60)
        assert len(evicted) == 1
        assert evicted[0].item_id == "expired"

    def test_removes_lowest_priority(self):
        reg = ContextRegistry()
        reg.upsert(_make_item("low", token_count=50, eviction_priority=0.1))
        reg.upsert(_make_item("high", token_count=50, eviction_priority=0.9))
        evicted = reg.evict(budget_tokens=60)
        assert evicted[0].item_id == "low"
        assert reg.get("high") is not None

    def test_high_priority_survives_pressure(self):
        reg = ContextRegistry()
        reg.upsert(_make_item("critical", token_count=30, eviction_priority=1.0))
        reg.upsert(_make_item("disposable", token_count=30, eviction_priority=0.0))
        evicted = reg.evict(budget_tokens=40)
        assert any(e.item_id == "disposable" for e in evicted)
        assert reg.get("critical") is not None


class TestBudgetUsage:
    def test_correct_per_tag_counts(self):
        reg = ContextRegistry()
        reg.upsert(_make_item("a", token_count=10, source_tag="memory"))
        reg.upsert(_make_item("b", token_count=20, source_tag="memory"))
        reg.upsert(_make_item("c", token_count=15, source_tag="file"))
        usage = reg.budget_usage()
        assert usage["memory"] == 30
        assert usage["file"] == 15


class TestEmpty:
    def test_empty_registry(self):
        reg = ContextRegistry()
        assert reg.count() == 0
        assert reg.total_tokens() == 0
        assert reg.query() == []
        assert reg.render("system", budget_tokens=100) == []
        assert reg.evict(budget_tokens=100) == []
        assert reg.budget_usage() == {}
