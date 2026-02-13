"""Context manager benchmarks — eviction and rendering throughput.

Why benchmark context: the context window is the most constrained resource
in agent turns. Eviction strategy performance directly affects response
quality (wrong eviction = lost context) and latency (slow eviction = slow turns).
"""

from __future__ import annotations

from silas.benchmarks.runner import benchmark
from silas.core.context_manager import LiveContextManager
from silas.core.token_counter import HeuristicTokenCounter, TokenBudget
from silas.models.context import ContextItem, ContextZone


def _make_context_mgr(budget: int = 4000) -> LiveContextManager:
    counter = HeuristicTokenCounter()
    token_budget = TokenBudget(total=budget, reserved_system=200, reserved_output=200)
    return LiveContextManager(token_budget=token_budget, token_counter=counter)


def _make_item(zone: ContextZone, text: str) -> ContextItem:
    return ContextItem(zone=zone, content=text)


@benchmark(name="context.add_and_render", tags=["context"], iterations=100)
def bench_add_and_render() -> None:
    """Add items across zones and render — measures context assembly cost."""
    mgr = _make_context_mgr()
    scope = "bench_scope"
    for i in range(20):
        mgr.add(scope, _make_item(ContextZone.conversation, f"user message {i}"))
    mgr.render(scope, turn_number=20)


@benchmark(name="context.eviction_under_pressure", tags=["context", "eviction"], iterations=100)
def bench_eviction() -> None:
    """Fill context well past budget and force eviction — measures eviction cost.

    Why a small budget: forces the eviction path on every add, which is the
    hot path we care about in production when conversations get long.
    """
    mgr = _make_context_mgr(budget=1000)
    scope = "bench_evict"
    for i in range(50):
        mgr.add(scope, _make_item(ContextZone.conversation, f"msg {i} " * 20))
        mgr.enforce_budget(scope, turn_number=i, current_goal=None)
