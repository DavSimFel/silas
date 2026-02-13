"""Gate evaluation benchmarks — measures policy/quality gate throughput.

Why benchmark gates: gates run on every agent response (spec §7). Slow gate
evaluation adds latency to every turn. We need to know the baseline cost
of predicate-based gates vs script-based gates.
"""

from __future__ import annotations

from silas.benchmarks.runner import benchmark
from silas.gates.predicates import PredicateChecker
from silas.gates.runner import SilasGateRunner
from silas.models.gates import (
    Gate,
    GateLane,
    GateProvider,
    GateTrigger,
    GateType,
)


def _make_predicate_gate(name: str = "length_check") -> Gate:
    return Gate(
        name=name,
        on=GateTrigger.every_agent_response,
        lane=GateLane.policy,
        provider=GateProvider.predicate,
        type=GateType.numeric_range,
        check="response_length",
        config={"min": 1, "max": 10000},
    )


def _make_runner() -> SilasGateRunner:
    return SilasGateRunner(predicate_checker=PredicateChecker())


@benchmark(name="gate.single_predicate", tags=["gate", "predicate"], iterations=200)
async def bench_single_predicate() -> None:
    """Single predicate gate evaluation — the fastest gate path."""
    runner = _make_runner()
    gate = _make_predicate_gate()
    ctx: dict[str, object] = {"response_length": 500}
    await runner.check_gate(gate, ctx)


@benchmark(name="gate.multi_gate_pipeline", tags=["gate", "pipeline"], iterations=100)
async def bench_multi_gate() -> None:
    """Evaluate a pipeline of 5 gates — simulates realistic gate configuration."""
    runner = _make_runner()
    gates = [_make_predicate_gate(f"gate_{i}") for i in range(5)]
    ctx: dict[str, object] = {"response_length": 500}
    await runner.check_gates(gates, ctx)
