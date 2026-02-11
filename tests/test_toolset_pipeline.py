from __future__ import annotations

from datetime import datetime, timezone

from silas.models.skills import SkillMetadata
from silas.models.work import WorkItem, WorkItemType
from silas.tools.approval_required import ApprovalRequiredToolset
from silas.tools.filtered import FilteredToolset
from silas.tools.prepared import PreparedToolset
from silas.tools.resolver import LiveSkillResolver
from silas.tools.skill_toolset import FunctionToolset, SkillToolset, ToolDefinition


class _FakeSkillLoader:
    def __init__(self, metadata_by_name: dict[str, SkillMetadata]) -> None:
        self._metadata_by_name = metadata_by_name
        self.loaded: list[str] = []

    def scan(self) -> list[SkillMetadata]:
        return [item.model_copy(deep=True) for item in self._metadata_by_name.values()]

    def load_metadata(self, skill_name: str) -> SkillMetadata:
        self.loaded.append(skill_name)
        return self._metadata_by_name[skill_name].model_copy(deep=True)

    def load_full(self, skill_name: str) -> str:
        return f"# {skill_name}"

    def resolve_script(self, skill_name: str, script_path: str) -> str:
        return f"/skills/{skill_name}/{script_path}"

    def validate(self, skill_name: str) -> dict[str, object]:
        return {"skill": skill_name, "ok": True}

    def import_external(self, source: str, format_hint: str | None = None) -> dict[str, object]:
        return {"source": source, "format_hint": format_hint, "ok": True}


def _tool(
    name: str,
    *,
    requires_approval: bool = False,
) -> ToolDefinition:
    def _handler(arguments: dict[str, object]) -> object:
        return {"tool": name, "arguments": dict(arguments)}

    return ToolDefinition(
        name=name,
        description=f"{name} description",
        input_schema={"type": "object"},
        handler=_handler,
        requires_approval=requires_approval,
    )


def _work_item(
    work_item_id: str,
    *,
    skills: list[str] | None = None,
    parent: str | None = None,
) -> WorkItem:
    return WorkItem(
        id=work_item_id,
        type=WorkItemType.task,
        title=f"Work item {work_item_id}",
        body="do work",
        skills=skills or [],
        parent=parent,
        created_at=datetime.now(timezone.utc),
    )


def test_skill_toolset_exposes_base_and_skill_tools() -> None:
    base = FunctionToolset([_tool("shell_exec"), _tool("web_search")])
    metadata = [
        SkillMetadata(name="ops-triage", description="Triages operations queue"),
        SkillMetadata(name="billing", description="Billing integration"),
    ]

    toolset = SkillToolset(base_toolset=base, skill_metadata=metadata)

    names = [tool.name for tool in toolset.list_tools()]
    assert names == ["shell_exec", "web_search", "ops-triage", "billing"]


def test_prepared_toolset_enriches_role_specific_data() -> None:
    base = FunctionToolset([_tool("shell_exec")])
    prepared = PreparedToolset(inner=base, agent_role="proxy")

    tools = prepared.list_tools()

    assert len(tools) == 1
    assert "proxy" in tools[0].description
    assert tools[0].input_schema["x-silas-agent-role"] == "proxy"
    assert tools[0].metadata["prepared_for_role"] == "proxy"


def test_filtered_toolset_removes_disallowed_tools() -> None:
    base = FunctionToolset([_tool("shell_exec"), _tool("python_exec"), _tool("web_search")])
    filtered = FilteredToolset(inner=base, allowed_tools=["shell_exec", "web_search"])

    names = [tool.name for tool in filtered.list_tools()]
    blocked = filtered.call("python_exec", {"code": "print(1)"})

    assert names == ["shell_exec", "web_search"]
    assert blocked.status == "filtered"


def test_approval_required_toolset_pauses_when_approval_needed() -> None:
    base = FunctionToolset([_tool("delete_records", requires_approval=True)])
    guarded = ApprovalRequiredToolset(inner=base)

    paused = guarded.call("delete_records", {"target": "all"})

    assert paused.status == "approval_required"
    assert paused.approval_request is not None
    assert paused.approval_request.tool_name == "delete_records"


def test_approval_required_toolset_resume_executes_when_approved() -> None:
    base = FunctionToolset([_tool("delete_records", requires_approval=True)])
    guarded = ApprovalRequiredToolset(inner=base)

    paused = guarded.call("delete_records", {"target": "all"})
    assert paused.approval_request is not None

    resumed = guarded.resume(paused.approval_request.request_id, approved=True)

    assert resumed.status == "ok"
    assert resumed.output == {"tool": "delete_records", "arguments": {"target": "all"}}


def test_approval_required_toolset_resume_decline_returns_denied() -> None:
    base = FunctionToolset([_tool("delete_records", requires_approval=True)])
    guarded = ApprovalRequiredToolset(inner=base)

    paused = guarded.call("delete_records", {"target": "all"})
    assert paused.approval_request is not None

    denied = guarded.resume(paused.approval_request.request_id, approved=False)

    assert denied.status == "denied"


def test_skill_resolver_resolves_skills_from_work_item_and_parent_inheritance() -> None:
    loader = _FakeSkillLoader(
        {
            "ops-triage": SkillMetadata(name="ops-triage", description="ops"),
            "billing": SkillMetadata(name="billing", description="billing"),
        }
    )

    parent = _work_item("parent", skills=["ops-triage", "billing"])
    child = _work_item("child", parent="parent")

    resolver = LiveSkillResolver(skill_loader=loader, work_item_lookup={"parent": parent}.get)
    resolved = resolver.resolve_for_work_item(child)

    assert [item.name for item in resolved] == ["ops-triage", "billing"]
    assert loader.loaded == ["ops-triage", "billing"]


def test_prepare_toolset_builds_canonical_chain_and_runs_end_to_end() -> None:
    loader = _FakeSkillLoader(
        {
            "ops-triage": SkillMetadata(
                name="ops-triage",
                description="triage actions",
                requires_approval=True,
            )
        }
    )

    resolver = LiveSkillResolver(skill_loader=loader)
    base = FunctionToolset([_tool("shell_exec"), _tool("web_search")])
    work_item = _work_item("wi-1", skills=["ops-triage"])

    toolset = resolver.prepare_toolset(
        work_item=work_item,
        agent_role="executor",
        base_toolset=base,
        allowed_tools=["shell_exec", "ops-triage"],
    )

    assert isinstance(toolset, ApprovalRequiredToolset)
    assert isinstance(toolset.inner, FilteredToolset)
    assert isinstance(toolset.inner.inner, PreparedToolset)
    assert isinstance(toolset.inner.inner.inner, SkillToolset)

    names = [tool.name for tool in toolset.list_tools()]
    assert names == ["shell_exec", "ops-triage"]

    safe_result = toolset.call("shell_exec", {"cmd": "echo hi"})
    assert safe_result.status == "ok"

    paused = toolset.call("ops-triage", {"limit": 10})
    assert paused.status == "approval_required"
    assert paused.approval_request is not None

    resumed = toolset.resume(paused.approval_request.request_id, approved=True)
    assert resumed.status == "ok"
