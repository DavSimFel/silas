"""Tests for the agent quality evaluation framework."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from silas.benchmarks.quality.agent_eval import (
    AgentEvalRunner,
    EvalAgent,
    EvalCase,
    EvalVerdict,
)


@dataclass
class FakeRouteOutput:
    route: str


@dataclass
class FakePlanAction:
    plan_markdown: str


@dataclass
class FakePlannerOutput:
    plan_action: FakePlanAction | None


@dataclass
class FakeExecutorOutput:
    tool_calls: list[Any]


def test_proxy_greeting_eval_passes() -> None:
    runner = AgentEvalRunner()
    output = FakeRouteOutput(route="direct")
    results = runner.evaluate(EvalAgent.proxy, output)
    greeting_result = next(r for r in results if r.case_name == "proxy_routes_greeting")
    assert greeting_result.verdict == EvalVerdict.passed


def test_proxy_complex_eval_passes() -> None:
    runner = AgentEvalRunner()
    output = FakeRouteOutput(route="planner")
    results = runner.evaluate(EvalAgent.proxy, output)
    complex_result = next(r for r in results if r.case_name == "proxy_routes_complex_task")
    assert complex_result.verdict == EvalVerdict.passed


def test_planner_eval_passes() -> None:
    runner = AgentEvalRunner()
    output = FakePlannerOutput(plan_action=FakePlanAction(plan_markdown="# Plan\n- Step 1"))
    results = runner.evaluate(EvalAgent.planner, output)
    assert results[0].verdict == EvalVerdict.passed


def test_planner_eval_fails_no_plan() -> None:
    runner = AgentEvalRunner()
    output = FakePlannerOutput(plan_action=None)
    results = runner.evaluate(EvalAgent.planner, output)
    assert results[0].verdict == EvalVerdict.failed


def test_executor_eval_passes() -> None:
    runner = AgentEvalRunner()
    output = FakeExecutorOutput(tool_calls=[{"tool": "read_file"}])
    results = runner.evaluate(EvalAgent.executor, output)
    assert results[0].verdict == EvalVerdict.passed


def test_executor_eval_fails_empty() -> None:
    runner = AgentEvalRunner()
    output = FakeExecutorOutput(tool_calls=[])
    results = runner.evaluate(EvalAgent.executor, output)
    assert results[0].verdict == EvalVerdict.failed


def test_summary_counts() -> None:
    runner = AgentEvalRunner()
    runner.evaluate(EvalAgent.proxy, FakeRouteOutput(route="direct"))
    runner.evaluate(EvalAgent.executor, FakeExecutorOutput(tool_calls=[]))
    summary = runner.summary()
    # Proxy: 1 pass (greeting) + 1 fail (complex expects planner); Executor: 1 fail
    assert summary["passed"] >= 1
    assert summary["failed"] >= 1


def test_custom_eval_case() -> None:
    """Users can define custom eval cases."""

    def custom_check(output: Any) -> tuple[EvalVerdict, str]:
        if output == 42:
            return EvalVerdict.passed, "correct"
        return EvalVerdict.failed, "wrong"

    case = EvalCase(
        name="custom_test",
        agent=EvalAgent.proxy,
        input_data={},
        check=custom_check,
    )
    runner = AgentEvalRunner(cases=[case])
    results = runner.evaluate(EvalAgent.proxy, 42)
    assert results[0].verdict == EvalVerdict.passed
