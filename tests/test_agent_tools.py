"""Tests for WI-2: pydantic-ai tool loops on all 3 agents.

Verifies toolset composition, security invariants (research allowlist),
tool function behavior with mock deps, and agent backward compatibility.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from silas.tools.backends import RESEARCH_TOOL_ALLOWLIST
from silas.tools.common import AgentDeps, memory_search, web_search
from silas.tools.executor_tools import skill_exec
from silas.tools.planner_tools import request_research, validate_plan
from silas.tools.proxy_tools import context_inspect, tell_user
from silas.tools.toolsets import (
    build_executor_toolset,
    build_planner_toolset,
    build_proxy_toolset,
    get_tool_names,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Provide a temporary workspace directory for toolset builders."""
    return tmp_path


@pytest.fixture
def mock_memory_retriever() -> AsyncMock:
    """Mock MemoryRetriever that returns canned results."""
    retriever = AsyncMock()
    retriever.search = AsyncMock(return_value=["memory result 1", "memory result 2"])
    return retriever


@pytest.fixture
def mock_web_search_provider() -> AsyncMock:
    """Mock WebSearchProvider that returns canned results."""
    provider = AsyncMock()
    provider.search = AsyncMock(return_value=["web result 1"])
    return provider


@pytest.fixture
def mock_queue_router() -> AsyncMock:
    """Mock QueueRouter for testing research request enqueueing."""
    router = AsyncMock()
    router.route = AsyncMock()
    return router


@pytest.fixture
def agent_deps(
    workspace: Path,
    mock_memory_retriever: AsyncMock,
    mock_web_search_provider: AsyncMock,
    mock_queue_router: AsyncMock,
) -> AgentDeps:
    """Fully configured AgentDeps with mock dependencies."""
    return AgentDeps(
        workspace_path=workspace,
        memory_retriever=mock_memory_retriever,
        web_search_provider=mock_web_search_provider,
        queue_router=mock_queue_router,
    )


def _make_run_context(deps: AgentDeps) -> MagicMock:
    """Create a mock RunContext with the given deps.

    Why mock: RunContext is created by pydantic-ai internally during
    agent.run(). For unit-testing tool functions directly, we mock it.
    """
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


# ---------------------------------------------------------------------------
# AgentDeps construction
# ---------------------------------------------------------------------------


class TestAgentDeps:
    """Verify AgentDeps can be constructed with various dependency combos."""

    def test_minimal_deps(self, workspace: Path) -> None:
        deps = AgentDeps(workspace_path=workspace)
        assert deps.workspace_path == workspace
        assert deps.memory_retriever is None
        assert deps.web_search_provider is None
        assert deps.queue_router is None

    def test_full_deps(self, agent_deps: AgentDeps) -> None:
        assert agent_deps.memory_retriever is not None
        assert agent_deps.web_search_provider is not None
        assert agent_deps.queue_router is not None

    def test_backend_property_lazy(self, workspace: Path) -> None:
        """Backend property lazily creates a LocalBackend."""
        deps = AgentDeps(workspace_path=workspace)
        backend = deps.backend
        assert backend is not None
        # Second access returns same instance
        assert deps.backend is backend


# ---------------------------------------------------------------------------
# Toolset composition tests
# ---------------------------------------------------------------------------


class TestProxyToolset:
    """Verify proxy toolset contains the correct tools."""

    def test_contains_expected_tools(self, agent_deps: AgentDeps) -> None:
        bundle = build_proxy_toolset(agent_deps)
        names = get_tool_names(bundle)
        # Must have custom tools
        assert "memory_search" in names
        assert "web_search" in names
        assert "tell_user" in names
        assert "context_inspect" in names
        # Must have read-only console tools
        assert "read_file" in names
        assert "ls" in names
        assert "grep" in names

    def test_no_execute_tool(self, agent_deps: AgentDeps) -> None:
        """Proxy must not have execute tool."""
        bundle = build_proxy_toolset(agent_deps)
        names = get_tool_names(bundle)
        assert "execute" not in names


class TestPlannerToolset:
    """Verify planner toolset contains the correct tools."""

    def test_contains_expected_tools(self, agent_deps: AgentDeps) -> None:
        bundle = build_planner_toolset(agent_deps)
        names = get_tool_names(bundle)
        assert "memory_search" in names
        assert "request_research" in names
        assert "validate_plan" in names
        assert "read_file" in names
        assert "ls" in names

    def test_no_execution_tools(self, agent_deps: AgentDeps) -> None:
        bundle = build_planner_toolset(agent_deps)
        names = get_tool_names(bundle)
        assert "execute" not in names
        assert "skill_exec" not in names


class TestExecutorToolsetResearch:
    """Verify executor research mode enforces RESEARCH_TOOL_ALLOWLIST."""

    def test_contains_only_allowlisted_console_tools(self, agent_deps: AgentDeps) -> None:
        bundle = build_executor_toolset(agent_deps, mode="research")
        names = get_tool_names(bundle)
        console_names = {"read_file", "grep", "glob", "ls", "write_file", "edit_file", "execute"}
        present_console = set(names) & console_names
        assert present_console <= RESEARCH_TOOL_ALLOWLIST, (
            f"Research mode has disallowed console tools: {present_console - RESEARCH_TOOL_ALLOWLIST}"
        )

    def test_no_write_file(self, agent_deps: AgentDeps) -> None:
        bundle = build_executor_toolset(agent_deps, mode="research")
        names = get_tool_names(bundle)
        assert "write_file" not in names

    def test_no_edit_file(self, agent_deps: AgentDeps) -> None:
        bundle = build_executor_toolset(agent_deps, mode="research")
        names = get_tool_names(bundle)
        assert "edit_file" not in names

    def test_no_execute(self, agent_deps: AgentDeps) -> None:
        bundle = build_executor_toolset(agent_deps, mode="research")
        names = get_tool_names(bundle)
        assert "execute" not in names

    def test_no_skill_exec(self, agent_deps: AgentDeps) -> None:
        bundle = build_executor_toolset(agent_deps, mode="research")
        names = get_tool_names(bundle)
        assert "skill_exec" not in names

    def test_has_read_tools(self, agent_deps: AgentDeps) -> None:
        bundle = build_executor_toolset(agent_deps, mode="research")
        names = get_tool_names(bundle)
        assert "read_file" in names
        assert "grep" in names
        assert "ls" in names

    def test_has_custom_search_tools(self, agent_deps: AgentDeps) -> None:
        bundle = build_executor_toolset(agent_deps, mode="research")
        names = get_tool_names(bundle)
        assert "memory_search" in names
        assert "web_search" in names


class TestExecutorToolsetExecution:
    """Verify executor execution mode has full toolset."""

    def test_has_write_and_execute(self, agent_deps: AgentDeps) -> None:
        bundle = build_executor_toolset(agent_deps, mode="execution")
        names = get_tool_names(bundle)
        assert "write_file" in names
        assert "execute" in names
        assert "edit_file" in names

    def test_has_skill_exec(self, agent_deps: AgentDeps) -> None:
        bundle = build_executor_toolset(agent_deps, mode="execution")
        names = get_tool_names(bundle)
        assert "skill_exec" in names

    def test_has_read_tools(self, agent_deps: AgentDeps) -> None:
        bundle = build_executor_toolset(agent_deps, mode="execution")
        names = get_tool_names(bundle)
        assert "read_file" in names
        assert "ls" in names


class TestResearchAllowlistEnforcement:
    """Verify the RESEARCH_TOOL_ALLOWLIST constant is correct."""

    def test_no_mutation_tools_in_allowlist(self) -> None:
        mutation_tools = {"write_file", "edit_file", "execute", "skill_exec"}
        assert RESEARCH_TOOL_ALLOWLIST.isdisjoint(mutation_tools)

    def test_allowlist_contains_expected_tools(self) -> None:
        expected = {"read_file", "grep", "glob", "ls", "web_search", "memory_search"}
        assert expected == RESEARCH_TOOL_ALLOWLIST


# ---------------------------------------------------------------------------
# Tool function tests (with mock deps)
# ---------------------------------------------------------------------------


class TestMemorySearchTool:
    @pytest.mark.asyncio
    async def test_returns_results(self, agent_deps: AgentDeps) -> None:
        ctx = _make_run_context(agent_deps)
        result = await memory_search(ctx, "test query")
        assert "memory result 1" in result
        assert "memory result 2" in result

    @pytest.mark.asyncio
    async def test_no_retriever(self, workspace: Path) -> None:
        deps = AgentDeps(workspace_path=workspace)
        ctx = _make_run_context(deps)
        result = await memory_search(ctx, "test")
        assert "unavailable" in result


class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_returns_results(self, agent_deps: AgentDeps) -> None:
        ctx = _make_run_context(agent_deps)
        result = await web_search(ctx, "test query")
        assert "web result 1" in result

    @pytest.mark.asyncio
    async def test_no_provider(self, workspace: Path) -> None:
        deps = AgentDeps(workspace_path=workspace)
        ctx = _make_run_context(deps)
        result = await web_search(ctx, "test")
        assert "unavailable" in result


class TestTellUserTool:
    @pytest.mark.asyncio
    async def test_returns_confirmation(self, agent_deps: AgentDeps) -> None:
        ctx = _make_run_context(agent_deps)
        result = await tell_user(ctx, "hello user")
        assert "hello user" in result


class TestContextInspectTool:
    @pytest.mark.asyncio
    async def test_returns_placeholder(self, agent_deps: AgentDeps) -> None:
        ctx = _make_run_context(agent_deps)
        result = await context_inspect(ctx, "current scope")
        assert "current scope" in result


class TestRequestResearchTool:
    @pytest.mark.asyncio
    async def test_enqueues_via_router(self, agent_deps: AgentDeps) -> None:
        ctx = _make_run_context(agent_deps)
        result = await request_research(ctx, "What is X?", context="some context")
        assert "Research dispatched" in result
        assert "request_id=" in result
        # Verify the router was called
        agent_deps.queue_router.route.assert_called_once()  # type: ignore[union-attr]
        # Verify the message was a research_request
        call_args = agent_deps.queue_router.route.call_args  # type: ignore[union-attr]
        msg = call_args[0][0]
        assert msg.message_kind == "research_request"
        assert msg.sender == "planner"

    @pytest.mark.asyncio
    async def test_no_router(self, workspace: Path) -> None:
        deps = AgentDeps(workspace_path=workspace)
        ctx = _make_run_context(deps)
        result = await request_research(ctx, "question")
        assert "unavailable" in result


class TestValidatePlanTool:
    @pytest.mark.asyncio
    async def test_valid_plan(self, agent_deps: AgentDeps) -> None:
        ctx = _make_run_context(agent_deps)
        plan = "---\nid: test\ntype: task\ntitle: Test\n---\nDo stuff."
        result = await validate_plan(ctx, plan)
        assert "OK" in result or "passed" in result

    @pytest.mark.asyncio
    async def test_invalid_plan_no_frontmatter(self, agent_deps: AgentDeps) -> None:
        ctx = _make_run_context(agent_deps)
        result = await validate_plan(ctx, "no yaml here")
        assert "warning" in result.lower() or "failed" in result.lower()


class TestSkillExecTool:
    @pytest.mark.asyncio
    async def test_returns_placeholder(self, agent_deps: AgentDeps) -> None:
        ctx = _make_run_context(agent_deps)
        result = await skill_exec(ctx, "my_skill", {"arg1": "val1"})
        assert "my_skill" in result


# ---------------------------------------------------------------------------
# Agent backward compatibility
# ---------------------------------------------------------------------------


class TestProxyAgentBackwardCompat:
    """Verify proxy agent works with and without tools."""

    @pytest.mark.asyncio
    async def test_no_tools_fallback(self) -> None:
        """Proxy without tools falls back to deterministic routing."""
        from silas.agents.proxy import build_proxy_agent

        agent = build_proxy_agent(model="test")
        result = await agent.run("hello")
        assert result.output.route == "direct"
        assert result.output.reason == "phase1a_deterministic_fallback"

    @pytest.mark.asyncio
    async def test_with_tools_flag_but_no_llm(self, agent_deps: AgentDeps) -> None:
        """Proxy with tools but no LLM still falls back gracefully."""
        from silas.agents.proxy import build_proxy_agent

        bundle = build_proxy_toolset(agent_deps)
        agent = build_proxy_agent(model="test", use_tools=True, tool_bundle=bundle)
        # Without a real LLM, the agent init will fail and _llm_available=False
        # so it falls back to deterministic routing
        result = await agent.run("hello", deps=agent_deps)
        assert result.output.route == "direct"


class TestPlannerAgentBackwardCompat:
    """Verify planner works with and without tools."""

    @pytest.mark.asyncio
    async def test_no_tools_fallback(self) -> None:
        from silas.agents.planner import build_planner_agent

        agent = build_planner_agent(model="test")
        result = await agent.run("build a thing")
        assert result.output.plan_action is not None
        assert result.output.plan_action.plan_markdown is not None


class TestPlannerResearchTracking:
    """Verify planner research state tracking."""

    def test_track_dispatched_within_cap(self) -> None:
        from silas.agents.planner import PlannerAgent

        agent = PlannerAgent(model="test")
        assert agent.in_flight_research == 0
        assert agent.track_research_dispatched() is True
        assert agent.in_flight_research == 1

    def test_track_dispatched_at_cap(self) -> None:
        from silas.agents.planner import PlannerAgent

        agent = PlannerAgent(model="test")
        for _ in range(3):
            agent.track_research_dispatched()
        assert agent.track_research_dispatched() is False
        assert agent.in_flight_research == 3

    def test_track_completed(self) -> None:
        from silas.agents.planner import PlannerAgent

        agent = PlannerAgent(model="test")
        agent.track_research_dispatched()
        agent.track_research_completed()
        assert agent.in_flight_research == 0


class TestExecutorAgentBackwardCompat:
    """Verify executor works with and without tools."""

    @pytest.mark.asyncio
    async def test_no_tools_fallback(self) -> None:
        from silas.agents.executor_agent import build_executor_agent

        agent = build_executor_agent(model="test")
        result = await agent.run("do something")
        assert result.output.summary is not None
