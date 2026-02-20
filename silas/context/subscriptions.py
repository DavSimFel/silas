from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from silas.core.telemetry import get_tracer
from silas.models.context import ContextSubscription

_FILE_SUBSCRIPTION_TYPES = {"file", "file_lines"}

# ---------------------------------------------------------------------------
# Prometheus metrics (no-op fallback)
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter, Gauge

    SUBSCRIPTIONS_ACTIVE = Gauge("silas_subscriptions_active", "Currently active subscriptions")
    SUBSCRIPTIONS_REGISTERED_TOTAL = Counter(
        "silas_subscriptions_registered_total", "Total subscriptions registered"
    )
    SUBSCRIPTIONS_EVICTED_TOTAL = Counter(
        "silas_subscriptions_evicted_total",
        "Total subscriptions evicted",
        ["reason"],
    )
    SUBSCRIPTIONS_REFRESHED_TOTAL = Counter(
        "silas_subscriptions_refreshed_total", "Total subscription refreshes"
    )
    SUBSCRIPTIONS_MATERIALIZED_TOTAL = Counter(
        "silas_subscriptions_materialized_total",
        "Total subscription materializations",
        ["result"],
    )
    SUBSCRIPTION_TOKEN_BUDGET_USED = Gauge(
        "silas_subscription_token_budget_used",
        "Token budget used by subscriptions",
    )
except ImportError:  # pragma: no cover
    from silas.core.metrics import _NoOpMetric

    SUBSCRIPTIONS_ACTIVE = _NoOpMetric()  # type: ignore[assignment]
    SUBSCRIPTIONS_REGISTERED_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    SUBSCRIPTIONS_EVICTED_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    SUBSCRIPTIONS_REFRESHED_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    SUBSCRIPTIONS_MATERIALIZED_TOTAL = _NoOpMetric()  # type: ignore[assignment]
    SUBSCRIPTION_TOKEN_BUDGET_USED = _NoOpMetric()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------
_tracer = get_tracer(__name__)


class ContextSubscriptionManager:
    def __init__(self, subscriptions: list[ContextSubscription]) -> None:
        self._subscriptions: dict[str, ContextSubscription] = {
            subscription.sub_id: subscription.model_copy(deep=True)
            for subscription in subscriptions
        }
        self._last_mtime_ns: dict[str, int] = {}
        SUBSCRIPTIONS_ACTIVE.set(len(self._subscriptions))  # type: ignore[union-attr]
        self._update_token_budget_gauge()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, subscription: ContextSubscription) -> None:
        with _tracer.start_as_current_span("subscription.register"):
            self._subscriptions[subscription.sub_id] = subscription.model_copy(
                update={"created_at": datetime.now(UTC)}
            )
            SUBSCRIPTIONS_REGISTERED_TOTAL.inc()  # type: ignore[union-attr]
            SUBSCRIPTIONS_ACTIVE.set(len(self._subscriptions))  # type: ignore[union-attr]
            self._update_token_budget_gauge()

    def unregister(self, subscription_id: str) -> bool:
        removed = self._subscriptions.pop(subscription_id, None) is not None
        if removed:
            SUBSCRIPTIONS_EVICTED_TOTAL.labels(reason="manual").inc()  # type: ignore[union-attr]
            SUBSCRIPTIONS_ACTIVE.set(len(self._subscriptions))  # type: ignore[union-attr]
            self._update_token_budget_gauge()
        return removed

    def check_changes(self) -> list[dict[str, Any]]:
        with _tracer.start_as_current_span("subscription.check_changes") as span:
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

                SUBSCRIPTIONS_REFRESHED_TOTAL.inc()  # type: ignore[union-attr]
                changed.append(
                    {
                        "subscription_id": subscription.sub_id,
                        "sub_type": subscription.sub_type,
                        "target": subscription.target,
                        "mtime_ns": mtime_ns,
                    }
                )
            span.set_attribute("changes_detected", len(changed))
            return changed

    def materialize(self, subscription_id: str) -> str | None:
        with _tracer.start_as_current_span("subscription.materialize") as span:
            subscription = self._subscriptions.get(subscription_id)
            if subscription is None or not subscription.active:
                SUBSCRIPTIONS_MATERIALIZED_TOTAL.labels(result="miss").inc()  # type: ignore[union-attr]
                span.set_attribute("hit", False)
                return None
            if subscription.sub_type not in _FILE_SUBSCRIPTION_TYPES:
                SUBSCRIPTIONS_MATERIALIZED_TOTAL.labels(result="miss").inc()  # type: ignore[union-attr]
                span.set_attribute("hit", False)
                return None

            path = Path(subscription.target)
            if not path.exists() or not path.is_file():
                SUBSCRIPTIONS_MATERIALIZED_TOTAL.labels(result="miss").inc()  # type: ignore[union-attr]
                span.set_attribute("hit", False)
                return None

            SUBSCRIPTIONS_MATERIALIZED_TOTAL.labels(result="hit").inc()  # type: ignore[union-attr]
            span.set_attribute("hit", True)
            return path.read_text(encoding="utf-8")

    def get_active(self) -> list[ContextSubscription]:
        now = datetime.now(UTC)
        return [
            subscription.model_copy(deep=True)
            for subscription in self._subscriptions.values()
            if not _is_expired(subscription, now)
        ]

    def prune_expired(self) -> int:
        with _tracer.start_as_current_span("subscription.prune") as span:
            now = datetime.now(UTC)
            expired_ids = [
                subscription.sub_id
                for subscription in self._subscriptions.values()
                if _is_expired(subscription, now)
            ]
            for subscription_id in expired_ids:
                self._subscriptions.pop(subscription_id, None)
                self._last_mtime_ns.pop(subscription_id, None)
                SUBSCRIPTIONS_EVICTED_TOTAL.labels(reason="ttl").inc()  # type: ignore[union-attr]
            SUBSCRIPTIONS_ACTIVE.set(len(self._subscriptions))  # type: ignore[union-attr]
            self._update_token_budget_gauge()
            span.set_attribute("removed_count", len(expired_ids))
            return len(expired_ids)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_token_budget_gauge(self) -> None:
        total = sum(s.token_count for s in self._subscriptions.values())
        SUBSCRIPTION_TOKEN_BUDGET_USED.set(total)  # type: ignore[union-attr]


def _is_expired(subscription: ContextSubscription, now: datetime) -> bool:
    if not subscription.active:
        return True

    expires_at = getattr(subscription, "expires_at", None)
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None or expires_at.tzinfo.utcoffset(expires_at) is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= now

    return False


__all__ = ["ContextSubscriptionManager"]
