from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ValidationError
from silas.agents.structured import run_structured_agent, structured_fallback
from silas.channels.web import WebChannel
from silas.config import ContextConfig, load_config
from silas.models.agents import AgentResponse, InteractionMode, InteractionRegister, RouteDecision
from silas.models.skills import SkillMetadata
from silas.models.work import Expectation, WorkItem, WorkItemType
from silas.tools.approval_required import ApprovalRequiredToolset
from silas.tools.filtered import FilteredToolset
from silas.tools.prepared import PreparedToolset
from silas.tools.resolver import LiveSkillResolver
from silas.tools.skill_toolset import ToolDefinition


class _ValidationSchema(BaseModel):
    value: int


def _validation_error() -> ValidationError:
    try:
        _ValidationSchema.model_validate({"value": "not-an-int"})
    except ValidationError as err:
        return err
    raise AssertionError("validation should fail")


class _RunResult:
    def __init__(self, output: object) -> None:
        self.output = output


class _FlakyStructuredAgent:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> object:
        self.prompts.append(prompt)
        response = self._responses.pop(0)
        if isinstance(response, ValidationError):
            raise response
        return response


class _FakeSkillLoader:
    def __init__(self, metadata_by_name: dict[str, SkillMetadata | dict[str, object]]) -> None:
        self._metadata_by_name = metadata_by_name

    def load_metadata(self, skill_name: str) -> SkillMetadata | dict[str, object]:
        loaded = self._metadata_by_name[skill_name]
        if isinstance(loaded, SkillMetadata):
            return loaded.model_copy(deep=True)
        return dict(loaded)


def _work_item(work_item_id: str, *, skills: list[str] | None = None) -> WorkItem:
    return WorkItem(
        id=work_item_id,
        type=WorkItemType.task,
        title=f"Work item {work_item_id}",
        body="do work",
        skills=skills or [],
        created_at=datetime.now(UTC),
    )


def _tool(name: str, *, requires_approval: bool = False) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        input_schema={"type": "object"},
        requires_approval=requires_approval,
    )


@pytest.mark.parametrize(
    ("payload", "field", "expected"),
    [
        ({"exit_code": 0}, "exit_code", 0),
        ({"equals": "done"}, "equals", "done"),
        ({"contains": "needle"}, "contains", "needle"),
        ({"regex": r"\\d+"}, "regex", r"\\d+"),
        ({"output_lt": 10.0}, "output_lt", 10.0),
        ({"output_gt": -1.0}, "output_gt", -1.0),
        ({"file_exists": "./artifact.txt"}, "file_exists", "./artifact.txt"),
        ({"not_empty": True}, "not_empty", True),
    ],
)
def test_expectation_accepts_each_single_predicate(
    payload: dict[str, object],
    field: str,
    expected: object,
) -> None:
    expectation = Expectation(**payload)
    assert getattr(expectation, field) == expected


def test_expectation_rejects_zero_predicates() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Expectation()


def test_expectation_rejects_multiple_predicates() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Expectation(contains="x", not_empty=True)


def test_expectation_not_empty_false_is_treated_as_unset() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Expectation(not_empty=False)


@pytest.mark.asyncio
async def test_run_structured_agent_retries_once_after_validation_error() -> None:
    agent = _FlakyStructuredAgent([
        _validation_error(),
        _RunResult({"status": "ok"}),
    ])

    result = await run_structured_agent(agent, "route this request", call_name="planner")

    assert result == {"status": "ok"}
    assert len(agent.prompts) == 2
    assert agent.prompts[0] == "route this request"
    assert "[SCHEMA VALIDATION ERROR]" in agent.prompts[1]
    assert "value" in agent.prompts[1]


@pytest.mark.asyncio
async def test_run_structured_agent_returns_proxy_fallback_after_two_validation_failures() -> None:
    agent = _FlakyStructuredAgent([_validation_error(), _validation_error()])

    result = await run_structured_agent(
        agent,
        "route this request",
        call_name="proxy",
        default_context_profile="planning",
    )

    expected = structured_fallback("proxy", "planning")
    assert isinstance(result, RouteDecision)
    assert result.model_dump(mode="json") == expected.model_dump(mode="json")
    assert len(agent.prompts) == 2


def test_structured_fallback_for_planner_is_deterministic() -> None:
    fallback = structured_fallback("planner", "conversation")

    assert isinstance(fallback, AgentResponse)
    assert fallback.needs_approval is False
    assert fallback.plan_action is None
    assert "valid plan structure" in fallback.message


@pytest.mark.asyncio
async def test_web_channel_health_endpoint_shape() -> None:
    channel = WebChannel()
    transport = ASGITransport(app=channel.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["connections"] == 0
    assert payload["sessions"] == []


@pytest.mark.asyncio
async def test_web_channel_health_endpoint_sorts_sessions() -> None:
    channel = WebChannel()
    channel._websockets_by_session = {
        "session-z": object(),
        "session-a": object(),
    }
    transport = ASGITransport(app=channel.app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connections"] == 2
    assert payload["sessions"] == ["session-a", "session-z"]


def test_context_config_injects_profile_names_and_converts_to_token_budget() -> None:
    config = ContextConfig(
        default_profile="focused",
        profiles={
            "focused": {
                "chronicle_pct": 0.30,
                "memory_pct": 0.20,
                "workspace_pct": 0.20,
            }
        },
    )

    budget = config.as_token_budget()

    assert config.profiles["focused"].name == "focused"
    assert budget.profiles["focused"].name == "focused"
    assert budget.default_profile == "focused"


def test_context_config_rejects_unknown_default_profile_in_token_budget() -> None:
    config = ContextConfig(
        default_profile="missing",
        profiles={
            "conversation": {
                "chronicle_pct": 0.20,
                "memory_pct": 0.20,
                "workspace_pct": 0.20,
            }
        },
    )

    with pytest.raises(ValidationError, match="default_profile"):
        config.as_token_budget()


def test_load_config_applies_env_overrides_and_updates_route_profiles(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "silas.yaml"
    config_path.write_text(
        """
        silas:
          context:
            default_profile: focused
            profiles:
              focused:
                chronicle_pct: 0.25
                memory_pct: 0.20
                workspace_pct: 0.15
          stream:
            chunk_size: 20
        """,
        encoding="utf-8",
    )

    monkeypatch.setenv("SILAS_STREAM__CHUNK_SIZE", "64")

    settings = load_config(config_path)

    assert settings.stream.chunk_size == 64
    assert settings.context.profiles["focused"].name == "focused"

    decision = RouteDecision(
        route="direct",
        reason="loaded profile should be valid",
        response=AgentResponse(message="ok", needs_approval=False),
        interaction_register=InteractionRegister.status,
        interaction_mode=InteractionMode.confirm_only_when_required,
        context_profile="focused",
    )
    assert decision.context_profile == "focused"

    with pytest.raises(ValidationError, match="unknown context profile"):
        RouteDecision(
            route="direct",
            reason="conversation profile should now be unknown",
            response=AgentResponse(message="ok", needs_approval=False),
            interaction_register=InteractionRegister.status,
            interaction_mode=InteractionMode.confirm_only_when_required,
            context_profile="conversation",
        )


def test_load_config_rejects_non_mapping_top_level(tmp_path) -> None:
    config_path = tmp_path / "silas.yaml"
    config_path.write_text("- not-a-mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="top-level mapping"):
        load_config(config_path)


def test_toolset_chain_filters_disallowed_approval_tool_before_pause() -> None:
    loader = _FakeSkillLoader(
        {
            "dangerous-skill": SkillMetadata(
                name="dangerous-skill",
                description="Dangerous skill",
                requires_approval=True,
            )
        }
    )
    resolver = LiveSkillResolver(skill_loader=loader)
    work_item = _work_item("wi-filter", skills=["dangerous-skill"])

    toolset = resolver.prepare_toolset(
        work_item=work_item,
        agent_role="executor",
        base_toolset=[_tool("shell_exec")],
        allowed_tools=["shell_exec"],
    )

    assert isinstance(toolset, ApprovalRequiredToolset)
    assert isinstance(toolset.inner, FilteredToolset)
    assert isinstance(toolset.inner.inner, PreparedToolset)

    result = toolset.call("dangerous-skill", {"force": True})

    assert result.status == "filtered"
    assert toolset.pending_requests() == []


def test_toolset_chain_handles_pending_lifecycle_and_resume_errors() -> None:
    loader = _FakeSkillLoader(
        {
            "ops-triage": {
                "name": "ops-triage",
                "description": "Operations triage",
                "requires_approval": True,
                "script_args": {"limit": {"type": "integer"}},
            }
        }
    )
    resolver = LiveSkillResolver(skill_loader=loader)
    work_item = _work_item("wi-approve", skills=["ops-triage"])

    toolset = resolver.prepare_toolset(
        work_item=work_item,
        agent_role="executor",
        base_toolset=[_tool("shell_exec")],
        allowed_tools=["shell_exec", "ops-triage"],
    )

    listed = {tool.name: tool for tool in toolset.list_tools()}
    assert "ops-triage" in listed
    assert listed["ops-triage"].input_schema["x-silas-agent-role"] == "executor"
    assert listed["ops-triage"].input_schema["properties"]["limit"]["type"] == "integer"

    paused = toolset.call("ops-triage", {"limit": 5})
    assert paused.status == "approval_required"
    assert paused.approval_request is not None

    pending = toolset.pending_requests()
    assert len(pending) == 1
    pending[0].arguments["limit"] = 999

    pending_again = toolset.pending_requests()
    assert pending_again[0].arguments["limit"] == 5

    unknown = toolset.resume("missing-request", approved=True)
    assert unknown.status == "error"

    resumed = toolset.resume(paused.approval_request.request_id, approved=True)
    assert resumed.status == "ok"
    assert isinstance(resumed.output, dict)
    assert resumed.output["tool"] == "ops-triage"
    assert resumed.output["arguments"] == {"limit": 5}
    assert toolset.pending_requests() == []
