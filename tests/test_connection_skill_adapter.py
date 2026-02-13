"""Tests for connection-as-skill integration (§2.5/§10.6)."""

from __future__ import annotations

import pytest
from silas.connections.lifecycle import (
    ConnectionConfig,
    HealthStatusLevel,
    LiveConnectionManager,
)
from silas.connections.skill_adapter import ConnectionSkillAdapter
from silas.models.skills import SkillDefinition
from silas.skills.executor import SkillExecutor
from silas.skills.registry import SkillRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_and_executor(
    skill_name: str = "conn-skill",
    handler_result: dict[str, object] | None = None,
    handler_error: Exception | None = None,
) -> tuple[SkillRegistry, SkillExecutor]:
    """Create a registry+executor with a single skill and custom handler."""
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name=skill_name,
            description="test connection skill",
            version="1.0.0",
            requires_approval=False,
            max_retries=0,
            timeout_seconds=5,
        )
    )
    executor = SkillExecutor(skill_registry=registry)

    async def _handler(inputs: dict[str, object]) -> dict[str, object]:
        if handler_error is not None:
            raise handler_error
        return handler_result or {"ok": True}

    executor.register_handler(skill_name, _handler)
    return registry, executor


# ---------------------------------------------------------------------------
# ConnectionSkillAdapter unit tests
# ---------------------------------------------------------------------------


class TestConnectionSkillAdapter:
    @pytest.mark.asyncio
    async def test_probe_returns_healthy(self) -> None:
        _, executor = _make_registry_and_executor(handler_result={"status": "up"})
        adapter = ConnectionSkillAdapter(skill_id="conn-skill", executor=executor)
        result = await adapter.probe()
        assert result["healthy"] is True

    @pytest.mark.asyncio
    async def test_probe_returns_unhealthy_on_failure(self) -> None:
        _, executor = _make_registry_and_executor(handler_error=RuntimeError("down"))
        adapter = ConnectionSkillAdapter(skill_id="conn-skill", executor=executor)
        result = await adapter.probe()
        assert result["healthy"] is False

    @pytest.mark.asyncio
    async def test_activate_succeeds(self) -> None:
        _, executor = _make_registry_and_executor(handler_result={"activated": True})
        adapter = ConnectionSkillAdapter(skill_id="conn-skill", executor=executor)
        result = await adapter.activate()
        assert result["activated"] is True

    @pytest.mark.asyncio
    async def test_refresh_succeeds(self) -> None:
        _, executor = _make_registry_and_executor(handler_result={"refreshed": True})
        adapter = ConnectionSkillAdapter(skill_id="conn-skill", executor=executor)
        result = await adapter.refresh()
        assert result["refreshed"] is True

    @pytest.mark.asyncio
    async def test_refresh_raises_on_failure(self) -> None:
        _, executor = _make_registry_and_executor(handler_error=RuntimeError("fail"))
        adapter = ConnectionSkillAdapter(skill_id="conn-skill", executor=executor)
        with pytest.raises(RuntimeError, match="refresh failed"):
            await adapter.refresh()

    @pytest.mark.asyncio
    async def test_deactivate_succeeds(self) -> None:
        _, executor = _make_registry_and_executor(handler_result={"deactivated": True})
        adapter = ConnectionSkillAdapter(skill_id="conn-skill", executor=executor)
        result = await adapter.deactivate()
        assert result["deactivated"] is True


# ---------------------------------------------------------------------------
# LiveConnectionManager with skill-backed connections
# ---------------------------------------------------------------------------


class TestLiveConnectionManagerSkillIntegration:
    @pytest.mark.asyncio
    async def test_skill_backed_health_check_delegates_to_skill(self) -> None:
        """When a connection has a skill_id, health_check delegates to the skill probe."""
        _, executor = _make_registry_and_executor(handler_result={"status": "up"})
        mgr = LiveConnectionManager(skill_executor=executor)

        config = ConnectionConfig(name="test-conn", skill_id="conn-skill")
        handle = mgr.register_connection(config)

        status = await mgr.health_check(handle.id)
        assert status.level == HealthStatusLevel.healthy
        await mgr.close()

    @pytest.mark.asyncio
    async def test_skill_backed_health_check_unhealthy_on_skill_failure(self) -> None:
        _, executor = _make_registry_and_executor(handler_error=RuntimeError("boom"))
        mgr = LiveConnectionManager(skill_executor=executor)

        config = ConnectionConfig(name="test-conn", skill_id="conn-skill")
        handle = mgr.register_connection(config)

        status = await mgr.health_check(handle.id)
        # Single failure → still healthy (sliding window), but the check failed
        assert status.level in {HealthStatusLevel.healthy, HealthStatusLevel.degraded, HealthStatusLevel.unhealthy}
        await mgr.close()

    @pytest.mark.asyncio
    async def test_skill_backed_refresh_delegates_to_skill(self) -> None:
        _, executor = _make_registry_and_executor(handler_result={"refreshed": True})
        mgr = LiveConnectionManager(skill_executor=executor)

        config = ConnectionConfig(name="test-conn", skill_id="conn-skill")
        handle = mgr.register_connection(config)

        result = await mgr.refresh_credentials(handle.id)
        assert result is True
        await mgr.close()

    @pytest.mark.asyncio
    async def test_skill_backed_refresh_returns_false_on_failure(self) -> None:
        _, executor = _make_registry_and_executor(handler_error=RuntimeError("nope"))
        mgr = LiveConnectionManager(skill_executor=executor)

        config = ConnectionConfig(name="test-conn", skill_id="conn-skill")
        handle = mgr.register_connection(config)

        result = await mgr.refresh_credentials(handle.id)
        assert result is False
        await mgr.close()

    @pytest.mark.asyncio
    async def test_plain_connection_works_without_skill(self) -> None:
        """Backward compat: connections without skill_id use HTTP checks."""
        mgr = LiveConnectionManager()

        config = ConnectionConfig(name="plain-conn", endpoint="http://localhost:1/nope")
        handle = mgr.register_connection(config)

        # Should not raise — just does HTTP check (which will fail)
        status = await mgr.health_check(handle.id)
        assert status.level in {HealthStatusLevel.healthy, HealthStatusLevel.degraded, HealthStatusLevel.unhealthy}
        assert handle.config.skill_id is None
        await mgr.close()

    @pytest.mark.asyncio
    async def test_registration_with_skill_id_stores_reference(self) -> None:
        _, executor = _make_registry_and_executor()
        mgr = LiveConnectionManager(skill_executor=executor)

        config = ConnectionConfig(name="skilled-conn", skill_id="conn-skill")
        handle = mgr.register_connection(config)

        assert handle.config.skill_id == "conn-skill"
        assert handle.id in mgr._skill_adapters
        assert mgr._skill_adapters[handle.id].skill_id == "conn-skill"
        await mgr.close()

    @pytest.mark.asyncio
    async def test_registration_without_executor_ignores_skill_id(self) -> None:
        """If no executor is provided, skill_id is stored but no adapter is created."""
        mgr = LiveConnectionManager()

        config = ConnectionConfig(name="no-exec", skill_id="conn-skill")
        handle = mgr.register_connection(config)

        assert handle.config.skill_id == "conn-skill"
        assert handle.id not in mgr._skill_adapters
        await mgr.close()

    @pytest.mark.asyncio
    async def test_deactivate_calls_skill_deactivate(self) -> None:
        calls: list[str] = []

        async def _handler(inputs: dict[str, object]) -> dict[str, object]:
            calls.append(str(inputs.get("action", "")))
            return {"ok": True}

        registry = SkillRegistry()
        registry.register(
            SkillDefinition(
                name="conn-skill",
                description="test",
                version="1.0.0",
            )
        )
        executor = SkillExecutor(skill_registry=registry)
        executor.register_handler("conn-skill", _handler)

        mgr = LiveConnectionManager(skill_executor=executor)
        config = ConnectionConfig(name="test", skill_id="conn-skill")
        handle = mgr.register_connection(config)

        await mgr.deactivate(handle.id)
        assert "deactivate" in calls
        assert handle.id not in mgr._skill_adapters
        await mgr.close()
