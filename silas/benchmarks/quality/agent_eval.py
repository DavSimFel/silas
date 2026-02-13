"""Agent quality evaluation framework — structured assertions on agent output.

Why structured assertions over LLM-as-judge: deterministic, fast, reproducible,
and no API cost. LLM-as-judge can be layered on later (spec §20) once we have
a baseline of deterministic eval cases.

Each eval case defines an input, expected properties of the output, and a
check function. The framework collects pass/fail results per case.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvalAgent(StrEnum):
    proxy = "proxy"
    planner = "planner"
    executor = "executor"


class EvalVerdict(StrEnum):
    passed = "passed"
    failed = "failed"
    skipped = "skipped"


@dataclass(frozen=True, slots=True)
class EvalCase:
    """A single quality evaluation case.

    The check function receives the agent output and returns (verdict, reason).
    Why a callable instead of declarative matchers: agent outputs are complex
    nested structures — a function gives full flexibility without building
    a custom DSL.
    """

    name: str
    agent: EvalAgent
    input_data: dict[str, Any]
    check: Callable[[Any], tuple[EvalVerdict, str]]
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EvalResult:
    """Result of running a single eval case."""

    case_name: str
    agent: EvalAgent
    verdict: EvalVerdict
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "agent": self.agent.value,
            "verdict": self.verdict.value,
            "reason": self.reason,
        }


# --- Built-in eval cases for the three agents ---


def check_proxy_routes_greeting(output: Any) -> tuple[EvalVerdict, str]:
    """Greetings should be routed direct, not to planner."""
    if not hasattr(output, "route"):
        return EvalVerdict.skipped, "output missing 'route' attribute"
    if output.route == "direct":
        return EvalVerdict.passed, "correctly routed greeting as direct"
    return EvalVerdict.failed, f"expected route='direct', got '{output.route}'"


def check_proxy_routes_complex_to_planner(output: Any) -> tuple[EvalVerdict, str]:
    """Multi-step tasks should be routed to planner."""
    if not hasattr(output, "route"):
        return EvalVerdict.skipped, "output missing 'route' attribute"
    if output.route == "planner":
        return EvalVerdict.passed, "correctly routed complex task to planner"
    return EvalVerdict.failed, f"expected route='planner', got '{output.route}'"


def check_planner_produces_plan(output: Any) -> tuple[EvalVerdict, str]:
    """Planner output must contain a plan_action with markdown content."""
    if not hasattr(output, "plan_action") or output.plan_action is None:
        return EvalVerdict.failed, "planner output missing plan_action"
    if not output.plan_action.plan_markdown:
        return EvalVerdict.failed, "plan_action has empty plan_markdown"
    return EvalVerdict.passed, "planner produced a plan with markdown content"


def check_executor_has_tool_calls(output: Any) -> tuple[EvalVerdict, str]:
    """Executor output should contain at least one tool call."""
    if not hasattr(output, "tool_calls"):
        return EvalVerdict.skipped, "output missing 'tool_calls' attribute"
    if len(output.tool_calls) > 0:
        return EvalVerdict.passed, f"executor produced {len(output.tool_calls)} tool calls"
    return EvalVerdict.failed, "executor produced zero tool calls"


# Pre-defined eval cases — can be extended by users
BUILTIN_EVAL_CASES: list[EvalCase] = [
    EvalCase(
        name="proxy_routes_greeting",
        agent=EvalAgent.proxy,
        input_data={"message": "Hello!"},
        check=check_proxy_routes_greeting,
        tags=["routing", "proxy"],
    ),
    EvalCase(
        name="proxy_routes_complex_task",
        agent=EvalAgent.proxy,
        input_data={"message": "Build a REST API with auth, deploy to k8s, and set up monitoring"},
        check=check_proxy_routes_complex_to_planner,
        tags=["routing", "proxy"],
    ),
    EvalCase(
        name="planner_produces_plan",
        agent=EvalAgent.planner,
        input_data={"message": "Create a data pipeline with ETL and dashboard"},
        check=check_planner_produces_plan,
        tags=["planning", "planner"],
    ),
    EvalCase(
        name="executor_produces_tool_calls",
        agent=EvalAgent.executor,
        input_data={"message": "Read the config file and update the version"},
        check=check_executor_has_tool_calls,
        tags=["execution", "executor"],
    ),
]


class AgentEvalRunner:
    """Runs eval cases against agent outputs and collects results.

    Why separate from BenchmarkRunner: quality evals measure correctness,
    not performance. They need agent output (possibly from recorded fixtures)
    rather than timing loops.
    """

    def __init__(self, cases: list[EvalCase] | None = None) -> None:
        self._cases = cases or list(BUILTIN_EVAL_CASES)
        self._results: list[EvalResult] = []

    @property
    def results(self) -> list[EvalResult]:
        return list(self._results)

    def evaluate(self, agent: EvalAgent, output: Any) -> list[EvalResult]:
        """Run all cases for the given agent against the provided output."""
        results: list[EvalResult] = []
        for case in self._cases:
            if case.agent != agent:
                continue
            verdict, reason = case.check(output)
            result = EvalResult(
                case_name=case.name,
                agent=case.agent,
                verdict=verdict,
                reason=reason,
            )
            results.append(result)
            self._results.append(result)
        return results

    def summary(self) -> dict[str, int]:
        """Aggregate pass/fail/skip counts."""
        counts: dict[str, int] = {"passed": 0, "failed": 0, "skipped": 0}
        for r in self._results:
            counts[r.verdict.value] += 1
        return counts
