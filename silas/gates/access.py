from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from silas.models.gates import AccessLevel
from silas.models.messages import TaintLevel

_LEVEL_ORDER: tuple[str, ...] = ("anonymous", "authenticated", "trusted", "owner")
_DEFAULT_LEVELS: dict[str, AccessLevel] = {
    "anonymous": AccessLevel(
        description="Unauthenticated access",
        tools=[],
        requires=[],
    ),
    "authenticated": AccessLevel(
        description="Authenticated access",
        tools=[],
        requires=["authenticated"],
    ),
    "trusted": AccessLevel(
        description="Trusted access",
        tools=[],
        requires=["trusted"],
    ),
    "owner": AccessLevel(
        description="Owner access",
        tools=["*"],
        requires=[],
    ),
}


@dataclass(slots=True)
class _AccessState:
    level_name: str
    verified_gates: set[str] = field(default_factory=set)
    customer_context: dict[str, object] | None = None
    granted_at: datetime | None = None


class SilasAccessController:
    """Deterministic, per-connection access controller."""

    def __init__(
        self,
        owner_id: str,
        access_levels: Mapping[str, AccessLevel] | None = None,
        default_level: str = "anonymous",
    ) -> None:
        self.owner_id = owner_id
        self.default_level = default_level
        self._access_levels = self._build_access_levels(access_levels)
        if self.default_level not in self._access_levels:
            raise ValueError(f"default level must exist in access levels: {self.default_level}")

        self._state_by_connection: dict[str, _AccessState] = {}

    def update_access_levels(
        self,
        access_levels: Mapping[str, AccessLevel],
        *,
        reset_non_owner_state: bool = False,
    ) -> None:
        self._access_levels = self._build_access_levels(access_levels)
        if self.default_level not in self._access_levels:
            raise ValueError(f"default level must exist in access levels: {self.default_level}")

        if reset_non_owner_state:
            for connection_id in list(self._state_by_connection):
                if connection_id == self.owner_id:
                    continue
                self._state_by_connection.pop(connection_id, None)

    def gate_passed(
        self,
        connection_id: str,
        gate_name: str,
        *,
        taint: TaintLevel | None = None,
        customer_context: Mapping[str, object] | None = None,
    ) -> str:
        if self._is_owner(connection_id, taint):
            self._ensure_owner_state(connection_id)
            return "owner"

        state = self._state_for(connection_id)
        self._downgrade_if_expired(state)

        state.verified_gates.add(gate_name)
        next_level = self._highest_reachable_level(state.verified_gates)
        if self._rank(next_level) > self._rank(state.level_name):
            state.level_name = next_level
            state.granted_at = datetime.now(UTC)
            if customer_context is not None:
                state.customer_context = dict(customer_context)

        return state.level_name

    def get_access_level(self, connection_id: str, *, taint: TaintLevel | None = None) -> str:
        if self._is_owner(connection_id, taint):
            self._ensure_owner_state(connection_id)
            return "owner"

        state = self._state_for(connection_id)
        self._downgrade_if_expired(state)
        return state.level_name

    def get_allowed_tools(self, connection_id: str, *, taint: TaintLevel | None = None) -> list[str]:
        level = self.get_access_level(connection_id, taint=taint)
        return list(self._access_levels[level].tools)

    def filter_tools(
        self,
        connection_id: str,
        tool_names: Sequence[str],
        *,
        taint: TaintLevel | None = None,
    ) -> list[str]:
        allowed = self.get_allowed_tools(connection_id, taint=taint)
        if "*" in allowed:
            return list(tool_names)
        allowed_set = set(allowed)
        return [tool_name for tool_name in tool_names if tool_name in allowed_set]

    def get_customer_context(
        self,
        connection_id: str,
        *,
        taint: TaintLevel | None = None,
    ) -> dict[str, object] | None:
        if self._is_owner(connection_id, taint):
            return None

        state = self._state_for(connection_id)
        self._downgrade_if_expired(state)
        return dict(state.customer_context) if state.customer_context is not None else None

    def state_snapshot(self, connection_id: str) -> dict[str, object]:
        state = self._state_for(connection_id)
        return {
            "level_name": state.level_name,
            "verified_gates": sorted(state.verified_gates),
            "customer_context": dict(state.customer_context) if state.customer_context else None,
            "granted_at": state.granted_at,
        }

    def _ensure_owner_state(self, connection_id: str) -> None:
        state = self._state_by_connection.get(connection_id)
        if state is None:
            state = _AccessState(level_name="owner", granted_at=datetime.now(UTC))
            self._state_by_connection[connection_id] = state
            return

        state.level_name = "owner"
        if state.granted_at is None:
            state.granted_at = datetime.now(UTC)

    def _state_for(self, connection_id: str) -> _AccessState:
        state = self._state_by_connection.get(connection_id)
        if state is None:
            state = _AccessState(level_name=self.default_level)
            self._state_by_connection[connection_id] = state
        return state

    def _downgrade_if_expired(self, state: _AccessState) -> None:
        current_level = self._access_levels[state.level_name]
        if current_level.expires_after is None:
            return
        if state.granted_at is None:
            return

        expiry_seconds = current_level.expires_after
        if expiry_seconds <= 0:
            return
        deadline = state.granted_at + timedelta(seconds=expiry_seconds)
        if datetime.now(UTC) < deadline:
            return

        state.level_name = self.default_level
        state.granted_at = None
        state.customer_context = None
        state.verified_gates.clear()

    def _highest_reachable_level(self, verified_gates: set[str]) -> str:
        highest = self.default_level
        for level_name in self._ordered_levels():
            if level_name == "owner":
                continue
            requirements = self._access_levels[level_name].requires
            if set(requirements).issubset(verified_gates) and self._rank(level_name) >= self._rank(highest):
                highest = level_name
        return highest

    def _ordered_levels(self) -> list[str]:
        known = [name for name in _LEVEL_ORDER if name in self._access_levels]
        custom = sorted(name for name in self._access_levels if name not in _LEVEL_ORDER)
        return known + custom

    def _rank(self, level_name: str) -> int:
        try:
            return _LEVEL_ORDER.index(level_name)
        except ValueError:
            return len(_LEVEL_ORDER)

    def _is_owner(self, connection_id: str, taint: TaintLevel | None) -> bool:
        return connection_id == self.owner_id or taint == TaintLevel.owner

    def _build_access_levels(
        self,
        access_levels: Mapping[str, AccessLevel] | None,
    ) -> dict[str, AccessLevel]:
        merged = {
            level_name: level.model_copy(deep=True)
            for level_name, level in _DEFAULT_LEVELS.items()
        }
        if access_levels is None:
            return merged

        for level_name, level in access_levels.items():
            merged[level_name] = level.model_copy(deep=True)
        return merged


__all__ = ["SilasAccessController"]
