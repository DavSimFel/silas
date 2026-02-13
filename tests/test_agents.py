"""Tests for agent fallback behavior (planner, executor, proxy).

These test deterministic fallback mode — no LLM calls needed.
Agents gracefully degrade when the model is unavailable.
"""

from __future__ import annotations

import pytest
from silas.agents.executor_agent import ExecutorAgent, build_executor_agent
from silas.agents.planner import PlannerAgent, build_planner_agent
from silas.agents.proxy import build_proxy_agent
from silas.models.agents import AgentResponse, RouteDecision
from silas.models.execution import ExecutorAgentOutput

# --- Planner Agent ---


class TestPlannerAgent:
    def test_build_planner_agent(self) -> None:
        agent = build_planner_agent(model="nonexistent-model")
        assert isinstance(agent, PlannerAgent)

    @pytest.mark.asyncio
    async def test_fallback_produces_agent_response(self) -> None:
        """With no LLM, planner should return a deterministic AgentResponse."""
        agent = build_planner_agent(model="nonexistent-model")
        result = await agent.run("Create a hello world script")
        assert result.output is not None
        assert isinstance(result.output, AgentResponse)

    @pytest.mark.asyncio
    async def test_fallback_response_has_plan_action(self) -> None:
        agent = build_planner_agent(model="nonexistent-model")
        result = await agent.run("Build a REST API")
        # Fallback should include a plan_action with the request echoed
        assert result.output.plan_action is not None


# --- Executor Agent ---


class TestExecutorAgent:
    def test_build_executor_agent(self) -> None:
        agent = build_executor_agent(model="nonexistent-model")
        assert isinstance(agent, ExecutorAgent)

    @pytest.mark.asyncio
    async def test_fallback_produces_output(self) -> None:
        agent = build_executor_agent(model="nonexistent-model")
        result = await agent.run("Execute the deployment script")
        assert result.output is not None
        assert isinstance(result.output, ExecutorAgentOutput)
        # Fallback should indicate it couldn't get structured output
        assert "fallback" in result.output.summary.lower()


# --- Proxy Agent ---


class TestProxyAgent:
    @pytest.mark.asyncio
    async def test_fallback_produces_route_decision(self) -> None:
        agent = build_proxy_agent(model="nonexistent-model")
        result = await agent.run("What's the weather?")
        assert result.output is not None
        assert isinstance(result.output, RouteDecision)
        assert result.output.route in ("responder", "planner", "direct")
