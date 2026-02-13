"""LiveConnectionManager — full lifecycle management for external connections.

Tracks connection health over time using a sliding window of check results,
supports automatic credential refresh, and integrates with SilasScheduler
for periodic health checks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, Field

from silas.connections.skill_adapter import ConnectionSkillAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

HEALTH_WINDOW_SIZE = 10  # sliding window for health status decisions


class AuthStrategy(StrEnum):
    oauth2 = "oauth2"
    api_key = "api_key"
    basic = "basic"
    none = "none"


class ConnectionConfig(BaseModel):
    """Everything needed to register and monitor a connection."""

    name: str
    auth_strategy: AuthStrategy = AuthStrategy.none
    endpoint: str | None = None
    health_check_interval_s: int = 60
    # OAuth fields — only relevant when auth_strategy == oauth2
    token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    # Optional backing skill — when set, lifecycle ops delegate to the skill system
    skill_id: str | None = None


class HealthStatusLevel(StrEnum):
    healthy = "healthy"
    degraded = "degraded"
    unhealthy = "unhealthy"


class HealthStatus(BaseModel):
    level: HealthStatusLevel
    message: str = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConnectionHandle(BaseModel):
    id: str
    config: ConnectionConfig
    status: HealthStatusLevel = HealthStatusLevel.healthy
    last_health_check: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    active: bool = True


# ---------------------------------------------------------------------------
# Internal bookkeeping per connection
# ---------------------------------------------------------------------------


class _ConnectionState:
    """Mutable runtime state not exposed to callers directly."""

    __slots__ = ("handle", "health_history", "health_task")

    def __init__(self, handle: ConnectionHandle) -> None:
        self.handle = handle
        # Sliding window of booleans — True = check passed
        self.health_history: deque[bool] = deque(maxlen=HEALTH_WINDOW_SIZE)
        self.health_task: asyncio.Task[None] | None = None


# ---------------------------------------------------------------------------
# LiveConnectionManager
# ---------------------------------------------------------------------------


class LiveConnectionManager:
    """Manages the full lifecycle of external connections.

    Responsibilities:
      - Register / deactivate connections
      - Run health checks (HTTP or token-expiry based)
      - Auto-refresh OAuth tokens when a refresh_token is available
      - Schedule periodic checks via SilasScheduler or plain asyncio tasks
    """

    def __init__(
        self,
        scheduler: Any | None = None,
        http_client: httpx.AsyncClient | None = None,
        skill_executor: Any | None = None,
    ) -> None:
        self._connections: dict[str, _ConnectionState] = {}
        self._scheduler = scheduler  # optional SilasScheduler
        # Allow injection for testing; created lazily otherwise
        self._http_client = http_client
        self._owns_http_client = http_client is None
        # Callback hook for credential refresh — tests can override
        self.on_refresh: Any | None = None
        # Optional SkillExecutor for skill-backed connections
        self._skill_executor = skill_executor
        self._skill_adapters: dict[str, ConnectionSkillAdapter] = {}

    # -- registration -------------------------------------------------------

    def register_connection(self, config: ConnectionConfig) -> ConnectionHandle:
        connection_id = f"conn-{uuid.uuid4().hex[:12]}"
        handle = ConnectionHandle(id=connection_id, config=config)
        state = _ConnectionState(handle)
        self._connections[connection_id] = state

        # Create skill adapter if this connection is skill-backed
        if config.skill_id and self._skill_executor is not None:
            self._skill_adapters[connection_id] = ConnectionSkillAdapter(
                skill_id=config.skill_id, executor=self._skill_executor
            )

        # Kick off periodic health checks if we have an endpoint or token to watch
        if config.endpoint or config.auth_strategy == AuthStrategy.oauth2 or config.skill_id:
            self._schedule_health_checks(state)

        return handle

    # -- health checks ------------------------------------------------------

    async def health_check(self, connection_id: str) -> HealthStatus:
        state = self._connections.get(connection_id)
        if state is None:
            return HealthStatus(level=HealthStatusLevel.unhealthy, message="unknown connection")

        if not state.handle.active:
            return HealthStatus(level=HealthStatusLevel.unhealthy, message="connection deactivated")

        # Delegate to skill adapter if available
        adapter = self._skill_adapters.get(connection_id)
        if adapter is not None:
            probe_result = await adapter.probe()
            passed = bool(probe_result.get("healthy", False))
        else:
            passed = await self._run_single_check(state)

        state.health_history.append(passed)

        level = self._compute_level(state.health_history)
        now = datetime.now(UTC)
        state.handle.last_health_check = now
        state.handle.status = level

        return HealthStatus(level=level, message=self._level_message(level), checked_at=now)

    # -- credential refresh -------------------------------------------------

    async def refresh_credentials(self, connection_id: str) -> bool:
        """Attempt a credential refresh.  Returns True on success."""
        state = self._connections.get(connection_id)
        if state is None:
            return False

        # Delegate to skill adapter if available
        adapter = self._skill_adapters.get(connection_id)
        if adapter is not None:
            try:
                await adapter.refresh()
                return True
            except RuntimeError:
                return False

        config = state.handle.config
        if config.auth_strategy != AuthStrategy.oauth2 or not config.refresh_token:
            return False

        # Delegate to injected callback (real implementation would POST to token endpoint)
        if self.on_refresh is not None:
            result = self.on_refresh(connection_id, config.refresh_token)
            if asyncio.iscoroutine(result):
                result = await result
            if result:
                # Simulate new token with extended expiry
                state.handle.config = config.model_copy(
                    update={"token_expires_at": datetime.now(UTC) + timedelta(hours=1)}
                )
                return True
            return False

        # No callback — mark as failed so callers know refresh wasn't possible
        return False

    # -- listing ------------------------------------------------------------

    def list_connections(self) -> list[ConnectionHandle]:
        return [s.handle.model_copy(deep=True) for s in self._connections.values()]

    # -- deactivation -------------------------------------------------------

    async def deactivate(self, connection_id: str) -> None:
        state = self._connections.get(connection_id)
        if state is None:
            return

        # Notify skill adapter of deactivation
        adapter = self._skill_adapters.pop(connection_id, None)
        if adapter is not None:
            await adapter.deactivate()

        state.handle.active = False
        state.handle.status = HealthStatusLevel.unhealthy

        # Stop the periodic health-check task so it doesn't keep running
        if state.health_task is not None and not state.health_task.done():
            state.health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.health_task
            state.health_task = None

    # -- shutdown -----------------------------------------------------------

    async def close(self) -> None:
        """Gracefully shut down all connections and the HTTP client."""
        for cid in list(self._connections):
            await self.deactivate(cid)
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # -- internals ----------------------------------------------------------

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def _run_single_check(self, state: _ConnectionState) -> bool:
        """Run one health check — returns True if healthy."""
        config = state.handle.config

        # Token-expiry check for OAuth connections
        if (
            config.auth_strategy == AuthStrategy.oauth2
            and config.token_expires_at
            and config.token_expires_at <= datetime.now(UTC)
        ):
            # Token expired — try auto-refresh before declaring unhealthy
            refreshed = await self.refresh_credentials(state.handle.id)
            if not refreshed:
                return False

        # HTTP endpoint probe
        if config.endpoint:
            try:
                client = self._get_http_client()
                resp = await client.get(config.endpoint, timeout=5.0)
                return resp.status_code < 500
            except (httpx.HTTPError, OSError):
                return False

        # No endpoint and token is fine (or no token) — assume healthy
        return True

    @staticmethod
    def _compute_level(history: deque[bool]) -> HealthStatusLevel:
        """Derive health level from the sliding window of check results.

        Rules (from spec §2.5):
          - Last 3 consecutive failures → unhealthy
          - >80% failures in window → unhealthy
          - >50% failures in window → degraded
          - Otherwise → healthy
        """
        if not history:
            return HealthStatusLevel.healthy

        total = len(history)
        failures = total - sum(history)

        # Three consecutive recent failures → immediate unhealthy
        if total >= 3 and not any(list(history)[-3:]):
            return HealthStatusLevel.unhealthy

        failure_ratio = failures / total
        if failure_ratio > 0.8:
            return HealthStatusLevel.unhealthy
        if failure_ratio > 0.5:
            return HealthStatusLevel.degraded

        return HealthStatusLevel.healthy

    @staticmethod
    def _level_message(level: HealthStatusLevel) -> str:
        return {
            HealthStatusLevel.healthy: "all checks passing",
            HealthStatusLevel.degraded: "intermittent failures detected",
            HealthStatusLevel.unhealthy: "connection is down",
        }[level]

    def _schedule_health_checks(self, state: _ConnectionState) -> None:
        """Start periodic health checks — uses SilasScheduler if available,
        otherwise falls back to a plain asyncio background task."""
        interval = state.handle.config.health_check_interval_s
        cid = state.handle.id

        if self._scheduler is not None:
            # SilasScheduler integration
            async def _callback() -> None:
                await self.health_check(cid)

            try:
                self._scheduler.add_heartbeat(
                    name=f"health-{cid}",
                    interval_seconds=interval,
                    callback=_callback,
                )
            except Exception:
                logger.warning("scheduler registration failed for %s, using asyncio task", cid)
                self._start_bg_task(state)
        else:
            self._start_bg_task(state)

    def _start_bg_task(self, state: _ConnectionState) -> None:
        interval = state.handle.config.health_check_interval_s

        async def _loop() -> None:
            while state.handle.active:
                try:
                    await self.health_check(state.handle.id)
                except Exception:
                    logger.exception("health check error for %s", state.handle.id)
                await asyncio.sleep(interval)

        state.health_task = asyncio.create_task(_loop())


__all__ = [
    "AuthStrategy",
    "ConnectionConfig",
    "ConnectionHandle",
    "HealthStatus",
    "HealthStatusLevel",
    "LiveConnectionManager",
]
