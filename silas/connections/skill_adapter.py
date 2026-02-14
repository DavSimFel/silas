"""ConnectionSkillAdapter — wraps a connection as a skill-like interface.

Maps connection lifecycle operations to skill invocations:
  - connect()             → skill activate
  - health_check()        → skill probe
  - refresh_credentials() → skill refresh action
  - disconnect()          → skill deactivate
"""

from __future__ import annotations

import logging

from silas.skills.executor import SkillExecutor

logger = logging.getLogger(__name__)


class ConnectionSkillAdapter:
    """Bridges a connection to its backing skill via SkillExecutor.

    Each adapter instance is bound to a single skill_id and delegates
    connection lifecycle operations to the corresponding skill handler.
    """

    def __init__(self, skill_id: str, executor: SkillExecutor) -> None:
        self._skill_id = skill_id
        self._executor = executor

    @property
    def skill_id(self) -> str:
        return self._skill_id

    async def activate(self, params: dict[str, object] | None = None) -> dict[str, object]:
        """Invoke the skill's activate action (connect)."""
        inputs: dict[str, object] = {"action": "activate", **(params or {})}
        result = await self._executor.execute(self._skill_id, inputs)
        if not result.success:
            raise RuntimeError(f"skill '{self._skill_id}' activate failed: {result.error}")
        return dict(result.output)

    async def probe(self, params: dict[str, object] | None = None) -> dict[str, object]:
        """Invoke the skill's probe action (health_check)."""
        inputs: dict[str, object] = {"action": "probe", **(params or {})}
        result = await self._executor.execute(self._skill_id, inputs)
        return {"healthy": result.success, "output": dict(result.output), "error": result.error}

    async def refresh(self, params: dict[str, object] | None = None) -> dict[str, object]:
        """Invoke the skill's refresh action (refresh_credentials)."""
        inputs: dict[str, object] = {"action": "refresh", **(params or {})}
        result = await self._executor.execute(self._skill_id, inputs)
        if not result.success:
            raise RuntimeError(f"skill '{self._skill_id}' refresh failed: {result.error}")
        return dict(result.output)

    async def deactivate(self, params: dict[str, object] | None = None) -> dict[str, object]:
        """Invoke the skill's deactivate action (disconnect)."""
        inputs: dict[str, object] = {"action": "deactivate", **(params or {})}
        result = await self._executor.execute(self._skill_id, inputs)
        if not result.success:
            logger.warning("skill '%s' deactivate failed: %s", self._skill_id, result.error)
        return dict(result.output)


__all__ = ["ConnectionSkillAdapter"]
