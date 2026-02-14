from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from silas.core.subscriptions import ContextSubscriptionManager, _is_expired
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
