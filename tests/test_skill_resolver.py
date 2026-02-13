"""Tests for SkillResolver, PreparedToolset, FilteredToolset, ApprovalRequiredToolset."""

from __future__ import annotations

from silas.models.skills import SkillMetadata
from silas.models.work import WorkItem
from silas.skills.resolver import (
    ApprovalRequiredToolset,
    FilteredToolset,
    PreparedToolset,
    SkillResolver,
)
from silas.tools.skill_toolset import FunctionToolset, ToolDefinition

# --- Fixtures ---


def _make_skill(name: str, description: str = "test skill") -> SkillMetadata:
    return SkillMetadata(name=name, description=description)


def _make_work_item(skills: list[str] | None = None, **kwargs) -> WorkItem:
    defaults = {
        "id": "wi-1",
        "title": "Test work item",
        "body": "Do something",
        "type": "task",
    }
    defaults.update(kwargs)
    if skills is not None:
        defaults["skills"] = skills
    return WorkItem(**defaults)


def _make_tool(name: str, requires_approval: bool = False) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Tool {name}",
        handler=lambda args, n=name: {"tool": n, "args": args},
        requires_approval=requires_approval,
    )


class FakeLoader:
    """Minimal fake loader that returns pre-configured metadata."""

    def __init__(self, skills: dict[str, SkillMetadata]) -> None:
        self._skills = skills

    def load_metadata(self, skill_name: str) -> SkillMetadata:
        if skill_name not in self._skills:
            raise ValueError(f"skill not found: {skill_name}")
        return self._skills[skill_name]


# --- SkillResolver tests ---


class TestSkillResolver:
    def test_resolve_known_skills(self) -> None:
        loader = FakeLoader({"alpha": _make_skill("alpha"), "beta": _make_skill("beta")})
        resolver = SkillResolver(loader=loader)
        wi = _make_work_item(skills=["alpha", "beta"])

        result = resolver.resolve_for_work_item(wi)

        assert len(result) == 2
        assert result[0].name == "alpha"
        assert result[1].name == "beta"

    def test_resolve_missing_skill_returns_partial(self) -> None:
        loader = FakeLoader({"alpha": _make_skill("alpha")})
        resolver = SkillResolver(loader=loader)
        wi = _make_work_item(skills=["alpha", "missing"])

        result = resolver.resolve_for_work_item(wi)

        # Should return alpha, skip missing
        assert len(result) == 1
        assert result[0].name == "alpha"

    def test_resolve_empty_skills_no_parent(self) -> None:
        loader = FakeLoader({})
        resolver = SkillResolver(loader=loader)
        wi = _make_work_item(skills=[])

        result = resolver.resolve_for_work_item(wi)
        assert result == []

    def test_resolve_inherits_from_parent(self) -> None:
        loader = FakeLoader({"parent-skill": _make_skill("parent-skill")})
        # Parent resolver returns skill names based on work item
        parent_resolver = lambda wi: ["parent-skill"]  # noqa: E731
        resolver = SkillResolver(loader=loader, parent_resolver=parent_resolver)
        wi = _make_work_item(skills=[])

        result = resolver.resolve_for_work_item(wi)

        assert len(result) == 1
        assert result[0].name == "parent-skill"

    def test_resolve_no_inherit_when_skills_declared(self) -> None:
        loader = FakeLoader({
            "own-skill": _make_skill("own-skill"),
            "parent-skill": _make_skill("parent-skill"),
        })
        parent_resolver = lambda wi: ["parent-skill"]  # noqa: E731
        resolver = SkillResolver(loader=loader, parent_resolver=parent_resolver)
        wi = _make_work_item(skills=["own-skill"])

        result = resolver.resolve_for_work_item(wi)

        # Should use own skills, not parent's
        assert len(result) == 1
        assert result[0].name == "own-skill"


# --- PreparedToolset tests ---


class TestPreparedToolset:
    def test_passes_through_list_and_call(self) -> None:
        base = FunctionToolset([_make_tool("t1")])
        prepared = PreparedToolset(inner=base, agent_role="executor")

        tools = prepared.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "t1"

        result = prepared.call("t1", {"x": 1})
        assert result.status == "ok"

    def test_stores_agent_role(self) -> None:
        base = FunctionToolset([])
        prepared = PreparedToolset(inner=base, agent_role="planner")
        assert prepared.agent_role == "planner"


# --- FilteredToolset tests ---


class TestFilteredToolset:
    def test_only_lists_allowed_tools(self) -> None:
        base = FunctionToolset([_make_tool("t1"), _make_tool("t2"), _make_tool("t3")])
        filtered = FilteredToolset(inner=base, allowed_tools=["t1", "t3"])

        names = [t.name for t in filtered.list_tools()]
        assert names == ["t1", "t3"]

    def test_blocks_disallowed_call(self) -> None:
        base = FunctionToolset([_make_tool("t1"), _make_tool("t2")])
        filtered = FilteredToolset(inner=base, allowed_tools=["t1"])

        result = filtered.call("t2", {})
        assert result.status == "filtered"
        assert "not in allowlist" in result.error

    def test_allows_permitted_call(self) -> None:
        base = FunctionToolset([_make_tool("t1")])
        filtered = FilteredToolset(inner=base, allowed_tools=["t1"])

        result = filtered.call("t1", {"key": "val"})
        assert result.status == "ok"


# --- ApprovalRequiredToolset tests ---


class TestApprovalRequiredToolset:
    def test_blocks_approval_required_tools(self) -> None:
        base = FunctionToolset([_make_tool("safe"), _make_tool("dangerous", requires_approval=True)])
        wrapped = ApprovalRequiredToolset(inner=base)

        result = wrapped.call("dangerous", {})
        assert result.status == "approval_required"
        assert "requires approval" in result.error

    def test_passes_through_safe_tools(self) -> None:
        base = FunctionToolset([_make_tool("safe"), _make_tool("dangerous", requires_approval=True)])
        wrapped = ApprovalRequiredToolset(inner=base)

        result = wrapped.call("safe", {})
        assert result.status == "ok"

    def test_lists_all_tools(self) -> None:
        base = FunctionToolset([_make_tool("safe"), _make_tool("dangerous", requires_approval=True)])
        wrapped = ApprovalRequiredToolset(inner=base)

        names = [t.name for t in wrapped.list_tools()]
        assert "safe" in names
        assert "dangerous" in names


# --- Full chain test ---


class TestPrepareToolset:
    def test_full_chain(self) -> None:
        """Test the complete wrapper chain: Skill → Prepared → Filtered → Approval."""
        loader = FakeLoader({"web-search": _make_skill("web-search")})
        resolver = SkillResolver(loader=loader)
        base = FunctionToolset([_make_tool("read"), _make_tool("write", requires_approval=True)])
        wi = _make_work_item(skills=["web-search"])

        toolset = resolver.prepare_toolset(
            work_item=wi,
            agent_role="executor",
            base_toolset=base,
            allowed_tools=["read", "write", "web-search"],
        )

        # Should be able to call read (safe, allowed)
        assert toolset.call("read", {}).status == "ok"

        # write requires approval
        assert toolset.call("write", {}).status == "approval_required"

        # web-search comes from skill, allowed
        result = toolset.call("web-search", {})
        # Skill tools without handlers return a default dict
        assert result.status == "ok"

    def test_chain_filters_unlisted_tools(self) -> None:
        loader = FakeLoader({})
        resolver = SkillResolver(loader=loader)
        base = FunctionToolset([_make_tool("read"), _make_tool("delete")])
        wi = _make_work_item(skills=[])

        toolset = resolver.prepare_toolset(
            work_item=wi,
            agent_role="executor",
            base_toolset=base,
            allowed_tools=["read"],  # delete not allowed
        )

        assert toolset.call("read", {}).status == "ok"
        assert toolset.call("delete", {}).status == "filtered"
