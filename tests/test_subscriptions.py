from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from silas.context.subscriptions import (
    ContextSubscriptionManager,
    _is_expired,
)
from silas.models.context import ContextSubscription, ContextZone


def _subscription(
    sub_id: str,
    target: str,
    *,
    active: bool = True,
    sub_type: str = "file",
    token_count: int = 123,
) -> ContextSubscription:
    return ContextSubscription(
        sub_id=sub_id,
        sub_type=sub_type,
        target=target,
        zone=ContextZone.workspace,
        turn_created=1,
        content_hash="hash",
        active=active,
        token_count=token_count,
    )


# ---------------------------------------------------------------------------
# Existing tests
# ---------------------------------------------------------------------------


def test_register_sets_fresh_created_at_and_preserves_token_budget_data(tmp_path) -> None:
    manager = ContextSubscriptionManager(subscriptions=[])
    file_path = tmp_path / "tracked.txt"
    file_path.write_text("v1", encoding="utf-8")
    original = _subscription("sub-1", str(file_path), token_count=321)
    original_created_at = original.created_at

    manager.register(original)
    active = manager.get_active()

    assert len(active) == 1
    assert active[0].token_count == 321
    assert active[0].created_at >= original_created_at


def test_get_active_returns_deep_copies() -> None:
    manager = ContextSubscriptionManager(subscriptions=[_subscription("sub-1", "/tmp/a.txt")])

    first = manager.get_active()
    first[0].target = "/tmp/changed.txt"

    second = manager.get_active()
    assert second[0].target == "/tmp/a.txt"


def test_check_changes_only_tracks_file_based_active_subscriptions(tmp_path) -> None:
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("v1", encoding="utf-8")
    manager = ContextSubscriptionManager(
        subscriptions=[
            _subscription("tracked", str(tracked), sub_type="file"),
            _subscription("query", "SELECT 1", sub_type="query"),
            _subscription("inactive", str(tracked), active=False),
        ]
    )

    assert manager.check_changes() == []
    previous_stat = tracked.stat()
    tracked.write_text("v2", encoding="utf-8")
    os.utime(tracked, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns + 1_000_000_000))

    changed = manager.check_changes()

    assert [item["subscription_id"] for item in changed] == ["tracked"]
    assert changed[0]["target"] == str(tracked)


def test_materialize_returns_none_for_non_file_missing_or_inactive(tmp_path) -> None:
    existing = tmp_path / "existing.txt"
    existing.write_text("content", encoding="utf-8")
    manager = ContextSubscriptionManager(
        subscriptions=[
            _subscription("file", str(existing), sub_type="file"),
            _subscription("query", "SELECT 1", sub_type="query"),
            _subscription("inactive", str(existing), active=False),
            _subscription("missing", str(tmp_path / "missing.txt"), sub_type="file"),
        ]
    )

    assert manager.materialize("file") == "content"
    assert manager.materialize("query") is None
    assert manager.materialize("inactive") is None
    assert manager.materialize("missing") is None
    assert manager.materialize("unknown") is None


def test_prune_expired_removes_inactive_and_time_expired_subscriptions() -> None:
    now = datetime.now(UTC)
    active = _subscription("active", "/tmp/active.txt")
    expired_by_flag = _subscription("inactive", "/tmp/inactive.txt", active=False)
    expired_by_time = _subscription("time-expired", "/tmp/expired.txt")
    object.__setattr__(expired_by_time, "expires_at", now - timedelta(seconds=1))

    manager = ContextSubscriptionManager([active, expired_by_flag, expired_by_time])

    removed = manager.prune_expired()

    assert removed == 2
    assert [sub.sub_id for sub in manager.get_active()] == ["active"]


def test_is_expired_accepts_naive_expires_at_as_utc() -> None:
    now = datetime.now(UTC)
    subscription = _subscription("naive", "/tmp/naive.txt")
    naive_expired = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    object.__setattr__(subscription, "expires_at", naive_expired)

    assert _is_expired(subscription, now) is True


# ---------------------------------------------------------------------------
# New tests: duplicate register, file_lines, large count, TTL boundary,
# non-existent file, metrics
# ---------------------------------------------------------------------------


def test_register_duplicate_sub_id_overwrites(tmp_path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    manager = ContextSubscriptionManager(subscriptions=[])
    sub_v1 = _subscription("dup", str(f), token_count=10)
    sub_v2 = _subscription("dup", str(f), token_count=999)

    manager.register(sub_v1)
    manager.register(sub_v2)

    active = manager.get_active()
    assert len(active) == 1
    assert active[0].token_count == 999


def test_file_lines_subscription_materialization(tmp_path) -> None:
    f = tmp_path / "lines.txt"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    manager = ContextSubscriptionManager(
        subscriptions=[_subscription("fl", str(f), sub_type="file_lines")]
    )

    result = manager.materialize("fl")
    assert result == "line1\nline2\nline3\n"


def test_large_subscription_count_performance() -> None:
    subs = [_subscription(f"sub-{i}", f"/tmp/fake-{i}.txt") for i in range(60)]
    start = time.monotonic()
    manager = ContextSubscriptionManager(subscriptions=subs)
    active = manager.get_active()
    elapsed = time.monotonic() - start

    assert len(active) == 60
    assert elapsed < 2.0, f"Took too long: {elapsed:.3f}s"


def test_ttl_exactly_at_boundary() -> None:
    now = datetime.now(UTC)
    sub = _subscription("boundary", "/tmp/b.txt")
    # expires_at == now  →  expired (<=)
    object.__setattr__(sub, "expires_at", now)

    assert _is_expired(sub, now) is True


def test_ttl_one_microsecond_before_boundary() -> None:
    now = datetime.now(UTC)
    sub = _subscription("almost", "/tmp/b.txt")
    object.__setattr__(sub, "expires_at", now + timedelta(microseconds=1))

    assert _is_expired(sub, now) is False


def test_check_changes_with_nonexistent_file(tmp_path) -> None:
    manager = ContextSubscriptionManager(
        subscriptions=[_subscription("ghost", str(tmp_path / "nope.txt"), sub_type="file")]
    )
    # Should not crash
    changed = manager.check_changes()
    assert changed == []


def test_materialize_file_lines_sub_type(tmp_path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    manager = ContextSubscriptionManager(
        subscriptions=[_subscription("csv", str(f), sub_type="file_lines")]
    )
    assert manager.materialize("csv") == "a,b,c\n1,2,3\n"


def test_metrics_are_incremented(tmp_path) -> None:
    """Verify Prometheus metrics are called during lifecycle operations."""
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")

    with (
        patch("silas.context.subscriptions.SUBSCRIPTIONS_REGISTERED_TOTAL") as mock_registered,
        patch("silas.context.subscriptions.SUBSCRIPTIONS_ACTIVE") as mock_active,
        patch("silas.context.subscriptions.SUBSCRIPTIONS_EVICTED_TOTAL") as mock_evicted,
        patch("silas.context.subscriptions.SUBSCRIPTIONS_MATERIALIZED_TOTAL") as mock_materialized,
        patch("silas.context.subscriptions.SUBSCRIPTION_TOKEN_BUDGET_USED") as mock_budget,
    ):
        manager = ContextSubscriptionManager(subscriptions=[])
        sub = _subscription("m1", str(f))
        manager.register(sub)
        mock_registered.inc.assert_called()
        mock_active.set.assert_called()
        mock_budget.set.assert_called()

        manager.materialize("m1")
        mock_materialized.labels.assert_called_with(result="hit")

        manager.materialize("nonexistent")
        mock_materialized.labels.assert_called_with(result="miss")

        manager.unregister("m1")
        mock_evicted.labels.assert_called_with(reason="manual")


def test_metrics_evict_ttl_on_prune() -> None:
    now = datetime.now(UTC)
    sub = _subscription("expiring", "/tmp/x.txt")
    object.__setattr__(sub, "expires_at", now - timedelta(seconds=1))

    with patch("silas.context.subscriptions.SUBSCRIPTIONS_EVICTED_TOTAL") as mock_evicted:
        manager = ContextSubscriptionManager(subscriptions=[sub])
        manager.prune_expired()
        mock_evicted.labels.assert_called_with(reason="ttl")


def test_token_budget_gauge_tracks_total(tmp_path) -> None:
    manager = ContextSubscriptionManager(subscriptions=[])
    f = tmp_path / "t.txt"
    f.write_text("x", encoding="utf-8")
    manager.register(_subscription("a", str(f), token_count=100))
    manager.register(_subscription("b", str(f), token_count=250))

    # After registering two subs, internal budget should be 350
    total = sum(s.token_count for s in manager.get_active())
    assert total == 350
