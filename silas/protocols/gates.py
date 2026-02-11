from __future__ import annotations

from typing import Protocol, runtime_checkable

from silas.models.gates import Gate, GateResult, GateTrigger


@runtime_checkable
class GateCheckProvider(Protocol):
    async def check(self, gate: Gate, context: dict[str, object]) -> GateResult: ...


@runtime_checkable
class GateRunner(Protocol):
    async def check_gates(
        self,
        gates: list[Gate],
        trigger: GateTrigger,
        context: dict[str, object],
    ) -> tuple[list[GateResult], list[GateResult], dict[str, object]]: ...

    async def check_gate(self, gate: Gate, context: dict[str, object]) -> GateResult: ...


__all__ = ["GateCheckProvider", "GateRunner"]
