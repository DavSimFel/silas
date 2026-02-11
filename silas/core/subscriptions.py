from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from silas.models.context import ContextSubscription

_FILE_SUBSCRIPTION_TYPES = {"file", "file_lines"}


class ContextSubscriptionManager:
    def __init__(self, subscriptions: list[ContextSubscription]) -> None:
        self._subscriptions: dict[str, ContextSubscription] = {
            subscription.sub_id: subscription.model_copy(deep=True)
            for subscription in subscriptions
        }
        self._last_mtime_ns: dict[str, int] = {}

    def register(self, subscription: ContextSubscription) -> None:
        self._subscriptions[subscription.sub_id] = subscription.model_copy(
            update={"created_at": datetime.now(timezone.utc)}
        )

    def unregister(self, subscription_id: str) -> bool:
        return self._subscriptions.pop(subscription_id, None) is not None

    def check_changes(self) -> list[dict[str, Any]]:
        changed: list[dict[str, Any]] = []
        for subscription in self.get_active():
            if subscription.sub_type not in _FILE_SUBSCRIPTION_TYPES:
                continue

            path = Path(subscription.target)
            if not path.exists():
                continue

            mtime_ns = path.stat().st_mtime_ns
            previous = self._last_mtime_ns.get(subscription.sub_id)
            self._last_mtime_ns[subscription.sub_id] = mtime_ns
            if previous is None or mtime_ns <= previous:
                continue

            changed.append(
                {
                    "subscription_id": subscription.sub_id,
                    "sub_type": subscription.sub_type,
                    "target": subscription.target,
                    "mtime_ns": mtime_ns,
                }
            )
        return changed

    def materialize(self, subscription_id: str) -> str | None:
        subscription = self._subscriptions.get(subscription_id)
        if subscription is None or not subscription.active:
            return None
        if subscription.sub_type not in _FILE_SUBSCRIPTION_TYPES:
            return None

        path = Path(subscription.target)
        if not path.exists() or not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def get_active(self) -> list[ContextSubscription]:
        now = datetime.now(timezone.utc)
        return [
            subscription.model_copy(deep=True)
            for subscription in self._subscriptions.values()
            if not _is_expired(subscription, now)
        ]

    def prune_expired(self) -> int:
        now = datetime.now(timezone.utc)
        expired_ids = [
            subscription.sub_id
            for subscription in self._subscriptions.values()
            if _is_expired(subscription, now)
        ]
        for subscription_id in expired_ids:
            self._subscriptions.pop(subscription_id, None)
            self._last_mtime_ns.pop(subscription_id, None)
        return len(expired_ids)


def _is_expired(subscription: ContextSubscription, now: datetime) -> bool:
    if not subscription.active:
        return True

    expires_at = getattr(subscription, "expires_at", None)
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None or expires_at.tzinfo.utcoffset(expires_at) is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= now

    return False


__all__ = ["ContextSubscriptionManager"]
