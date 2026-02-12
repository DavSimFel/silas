from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from silas.channels.web import WebChannel
from silas.config import SilasSettings
from silas.connections.manager import SilasConnectionManager
from silas.core.context_manager import LiveContextManager
from silas.core.token_counter import HeuristicTokenCounter
from silas.gates.runner import SilasGateRunner
from silas.models.connections import HealthCheckResult
from silas.models.context import ContextItem, ContextProfile, ContextZone, TokenBudget
from silas.models.gates import Gate, GateLane, GateProvider, GateResult, GateTrigger, GateType
from silas.models.messages import TaintLevel
from silas.models.review import BatchActionDecision, BatchActionItem, BatchProposal
from silas.models.work import Expectation, VerificationCheck
from silas.protocols.work import VerificationRunner
from silas.skills.loader import SilasSkillLoader
from silas.work.batch import BatchExecutor

from tests.fakes import FakeTokenCounter, sample_context_profile


class _FakeVerificationRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def run_checks(self, checks: list[VerificationCheck]) -> list[dict[str, object]]:
        self.calls.append([check.name for check in checks])
        return [
            {
                "name": check.name,
                "run": check.run,
                "passed": check.expect.not_empty is True,
            }
            for check in checks
        ]


class _StaticGateProvider:
    def __init__(self, result: GateResult) -> None:
        self._result = result

    async def check(self, gate: Gate, context: dict[str, object]) -> GateResult:
        del gate, context
        return self._result.model_copy(deep=True)


class _RaisingGateProvider:
    async def check(self, gate: Gate, context: dict[str, object]) -> GateResult:
        del gate, context
        raise RuntimeError("provider unavailable")


class _RecordingQualityProvider:
    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    async def check(self, gate: Gate, context: dict[str, object]) -> GateResult:
        self.contexts.append(dict(context))
        return GateResult(
            gate_name=gate.name,
            lane=GateLane.quality,
            action="continue",
            reason="observed",
        )


class _BatchStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute_action(self, action: str, payload: dict[str, object]) -> None:
        self.calls.append((action, dict(payload)))


class _RequestResponseProcess:
    def __init__(
        self,
        *,
        stdout_lines: list[str] | None = None,
        stderr_text: str = "",
        returncode: int = 0,
    ) -> None:
        self.returncode = returncode
        self._stderr_text = stderr_text
        self.received_input: bytes | None = None
        rendered_lines = [line if line.endswith("\n") else f"{line}\n" for line in (stdout_lines or [])]
        self._stdout_payload = "".join(rendered_lines)

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.received_input = input
        return self._stdout_payload.encode("utf-8"), self._stderr_text.encode("utf-8")


class _GateRunnerAllowDict:
    def run(self, action: str, payload: dict[str, object]) -> dict[str, object]:
        del action
        return {"allowed": payload["item_id"] != "blocked"}


def _gate(
    name: str,
    *,
    provider: GateProvider = GateProvider.predicate,
    trigger: GateTrigger = GateTrigger.every_user_message,
    promote_to_policy: bool = False,
) -> Gate:
    return Gate(
        name=name,
        on=trigger,
        provider=provider,
        type=GateType.custom_check,
        check="custom",
        config={},
        promote_to_policy=promote_to_policy,
    )


def _touch_script(skills_dir: Path, skill_name: str, script_name: str) -> Path:
    path = skills_dir / skill_name / script_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return path


def _context_budget(
    *,
    total: int = 40,
    system_max: int = 8,
    observation_mask_after_turns: int = 5,
) -> TokenBudget:
    profile = sample_context_profile("conversation")
    profiles = {profile.name: ContextProfile.model_validate(profile.model_dump(mode="python"))}
    return TokenBudget(
        total=total,
        system_max=system_max,
        observation_mask_after_turns=observation_mask_after_turns,
        profiles=profiles,
        default_profile="conversation",
    )


def _context_item(
    ctx_id: str,
    zone: ContextZone,
    *,
    content: str | None = None,
    token_count: int | None = None,
    turn_number: int = 1,
    source: str = "test",
    kind: str = "message",
    relevance: float = 1.0,
    pinned: bool = False,
    created_at: datetime | None = None,
) -> ContextItem:
    text = content or f"{ctx_id} content"
    tokens = token_count if token_count is not None else FakeTokenCounter().count(text)
    return ContextItem(
        ctx_id=ctx_id,
        zone=zone,
        content=text,
        token_count=tokens,
        turn_number=turn_number,
        source=source,
        taint=TaintLevel.owner,
        kind=kind,
        relevance=relevance,
        pinned=pinned,
        created_at=created_at or datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_verification_runner_protocol_flow_with_fake_runner() -> None:
    runner = _FakeVerificationRunner()
    checks = [
        VerificationCheck(
            name="lint",
            run="ruff check silas tests",
            expect=Expectation(not_empty=True),
        ),
        VerificationCheck(
            name="unit",
            run="pytest -q",
            expect=Expectation(contains="passed"),
        ),
    ]

    results = await runner.run_checks(checks)

    assert isinstance(runner, VerificationRunner)
    assert runner.calls == [["lint", "unit"]]
    assert [row["name"] for row in results] == ["lint", "unit"]


@pytest.mark.xfail(reason="SilasVerificationRunner implementation is not present in this phase")
def test_concrete_verification_runner_module_exists() -> None:
    from silas.verification.runner import SilasVerificationRunner  # noqa: PLC0415

    assert SilasVerificationRunner is not None


@pytest.mark.xfail(reason="WebSocket auth handshake/4001 semantics are not implemented in WebChannel")
def test_websocket_requires_auth_before_counting_connection() -> None:
    channel = WebChannel(scope_id="owner")

    with TestClient(channel.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text("unauthenticated payload")
            health = client.get("/health").json()
            assert health["connections"] == 0


@pytest.mark.asyncio
async def test_websocket_message_queue_keeps_session_scope_isolation() -> None:
    channel = WebChannel(scope_id="owner")

    await channel._handle_client_payload("hello from a", session_id="scope-a")
    await channel._handle_client_payload(
        json.dumps({"type": "message", "text": "hello from b", "sender_id": "guest"}),
        session_id="scope-b",
    )

    first_message, first_scope = await asyncio.wait_for(channel._incoming.get(), timeout=0.2)
    second_message, second_scope = await asyncio.wait_for(channel._incoming.get(), timeout=0.2)

    assert {first_scope, second_scope} == {"scope-a", "scope-b"}
    assert first_message.channel == "web"
    assert second_message.channel == "web"


def test_websocket_active_sessions_tracks_multiple_sessions() -> None:
    channel = WebChannel(scope_id="owner")

    with TestClient(channel.app) as client:
        with (
            client.websocket_connect("/ws?session=scope-a"),
            client.websocket_connect("/ws?session=scope-b"),
        ):
            flattened = sorted(
                {
                    session
                    for session_list in channel.active_sessions().values()
                    for session in session_list
                }
            )
            assert flattened == ["scope-a", "scope-b"]


@pytest.mark.asyncio
async def test_gate_runner_llm_provider_failure_falls_back_with_quality_flags() -> None:
    runner = SilasGateRunner(providers={GateProvider.llm: _RaisingGateProvider()})

    policy_results, quality_results, _ = await runner.check_gates(
        gates=[_gate("quality-llm", provider=GateProvider.llm)],
        trigger=GateTrigger.every_user_message,
        context={"message": "hello"},
    )

    assert policy_results == []
    assert len(quality_results) == 1
    assert quality_results[0].action == "continue"
    assert "provider_error" in quality_results[0].flags
    assert "quality_lane_violation" in quality_results[0].flags


@pytest.mark.asyncio
async def test_gate_runner_policy_mutation_is_visible_to_quality_lane() -> None:
    policy_provider = _StaticGateProvider(
        GateResult(
            gate_name="policy-mutation",
            lane=GateLane.policy,
            action="continue",
            reason="rewrite",
            modified_context={"message": "mutated by policy"},
        )
    )
    quality_provider = _RecordingQualityProvider()
    runner = SilasGateRunner(
        providers={
            GateProvider.custom: policy_provider,
            GateProvider.llm: quality_provider,
        }
    )

    policy_results, quality_results, merged_context = await runner.check_gates(
        gates=[
            _gate("policy-mutation", provider=GateProvider.custom),
            _gate("quality-observer", provider=GateProvider.llm),
        ],
        trigger=GateTrigger.every_user_message,
        context={"message": "original"},
    )

    assert len(policy_results) == 1
    assert len(quality_results) == 1
    assert merged_context["message"] == "mutated by policy"
    assert quality_provider.contexts[0]["message"] == "mutated by policy"


@pytest.mark.asyncio
async def test_gate_runner_promoted_llm_gate_runs_in_policy_lane() -> None:
    runner = SilasGateRunner(
        providers={
            GateProvider.llm: _StaticGateProvider(
                GateResult(
                    gate_name="llm-policy",
                    lane=GateLane.policy,
                    action="require_approval",
                    reason="high risk",
                )
            )
        }
    )

    policy_results, quality_results, _ = await runner.check_gates(
        gates=[
            _gate(
                "llm-policy",
                provider=GateProvider.llm,
                promote_to_policy=True,
            )
        ],
        trigger=GateTrigger.every_user_message,
        context={"message": "deploy to prod"},
    )

    assert len(policy_results) == 1
    assert quality_results == []
    assert policy_results[0].action == "require_approval"


def test_models_config_accepts_multiple_llm_provider_prefixes() -> None:
    settings = SilasSettings.model_validate(
        {
            "models": {
                "proxy": "openai:gpt-4.1-mini",
                "planner": "anthropic:claude-3-7-sonnet",
                "executor": "openrouter:anthropic/claude-haiku-4-5",
                "scorer": "local:llama3.1",
            }
        }
    )

    assert settings.models.proxy.startswith("openai:")
    assert settings.models.planner.startswith("anthropic:")
    assert settings.models.executor.startswith("openrouter:")
    assert settings.models.scorer.startswith("local:")


@pytest.mark.asyncio
async def test_gate_runner_provider_registration_is_case_insensitive() -> None:
    runner = SilasGateRunner(
        providers={
            "LLM": _StaticGateProvider(
                GateResult(
                    gate_name="provider-case",
                    lane=GateLane.quality,
                    action="continue",
                    reason="ok",
                )
            )
        }
    )

    result = await runner.check_gate(
        _gate("provider-case", provider=GateProvider.llm),
        {"message": "hello"},
    )

    assert result.lane == GateLane.quality
    assert result.action == "continue"


def test_context_budget_eviction_tiebreaker_prefers_oldest_on_equal_relevance() -> None:
    manager = LiveContextManager(
        token_budget=_context_budget(total=20, system_max=0),
        token_counter=HeuristicTokenCounter(),
    )
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    manager.add(
        "owner",
        _context_item(
            "old",
            ContextZone.chronicle,
            token_count=5,
            turn_number=1,
            relevance=0.1,
            created_at=base,
        ),
    )
    manager.add(
        "owner",
        _context_item(
            "new",
            ContextZone.chronicle,
            token_count=5,
            turn_number=2,
            relevance=0.1,
            created_at=base + timedelta(seconds=1),
        ),
    )

    evicted = manager.enforce_budget("owner", turn_number=3, current_goal=None)

    assert evicted == ["old"]


def test_context_budget_enforcement_does_not_evict_system_zone_items() -> None:
    manager = LiveContextManager(
        token_budget=_context_budget(total=12, system_max=2),
        token_counter=HeuristicTokenCounter(),
    )
    manager.add(
        "owner",
        _context_item(
            "sys-1",
            ContextZone.system,
            content="system directives",
            token_count=50,
            kind="system",
        ),
    )

    evicted = manager.enforce_budget("owner", turn_number=1, current_goal=None)

    assert evicted == []
    assert [item.ctx_id for item in manager.get_zone("owner", ContextZone.system)] == ["sys-1"]


def test_context_manager_recovers_to_default_profile_when_scope_profile_is_unknown() -> None:
    budget = _context_budget(total=30, system_max=0)
    manager = LiveContextManager(token_budget=budget, token_counter=HeuristicTokenCounter())
    manager.profile_by_scope["owner"] = "missing"

    manager.add("owner", _context_item("c1", ContextZone.chronicle, token_count=10, relevance=0.3))
    manager.add("owner", _context_item("c2", ContextZone.chronicle, token_count=10, relevance=0.2))

    manager.enforce_budget("owner", turn_number=2, current_goal=None)

    assert manager.profile_by_scope["owner"] == budget.default_profile


def test_context_masking_updates_usage_after_rendering() -> None:
    manager = LiveContextManager(
        token_budget=_context_budget(total=120, observation_mask_after_turns=1),
        token_counter=HeuristicTokenCounter(),
    )
    manager.add(
        "owner",
        _context_item(
            "tool-1",
            ContextZone.chronicle,
            content="raw tool output",
            kind="tool_result",
            source="shell_exec",
            turn_number=1,
        ),
    )

    before = manager.token_usage("owner")["chronicle"]
    rendered = manager.render("owner", turn_number=5)
    after = manager.token_usage("owner")["chronicle"]

    assert "[Result of shell_exec" in rendered
    assert after != before
    assert manager.get_zone("owner", ContextZone.chronicle)[0].masked is True


def test_batch_create_from_items_defaults_missing_fields() -> None:
    executor = BatchExecutor(work_item_store=_BatchStore())

    proposal = executor.create_batch_from_items(
        [
            {"title": "Only title"},
            {"id": "legacy-id", "title": "Legacy", "actor": "owner"},
        ],
        action="archive",
    )

    assert proposal.proposal_id.startswith("proposal:")
    assert proposal.items[0].title == "Only title"
    assert proposal.items[0].actor == "unknown"
    assert proposal.items[0].item_id
    assert proposal.items[1].item_id == "legacy-id"


def test_batch_execute_blocks_items_when_gate_runner_disallows_payload() -> None:
    store = _BatchStore()
    executor = BatchExecutor(work_item_store=store, gate_runner=_GateRunnerAllowDict())
    proposal = BatchProposal(
        proposal_id="proposal-1",
        action="archive",
        items=[
            BatchActionItem(item_id="blocked", title="Blocked", actor="alice"),
            BatchActionItem(item_id="ok", title="Allowed", actor="bob"),
        ],
    )

    results = executor.execute_batch(
        proposal,
        BatchActionDecision(proposal_id=proposal.proposal_id, verdict="approve"),
    )

    assert results[0]["item_id"] == "blocked"
    assert results[0]["success"] is False
    assert "blocked by gate runner" in str(results[0]["error"])
    assert [call[1]["item_id"] for call in store.calls] == ["ok"]


def test_batch_edit_selection_ignores_unknown_ids() -> None:
    store = _BatchStore()
    executor = BatchExecutor(work_item_store=store)
    proposal = BatchProposal(
        proposal_id="proposal-1",
        action="archive",
        items=[BatchActionItem(item_id="i1", title="First", actor="alice")],
    )

    results = executor.execute_batch(
        proposal,
        BatchActionDecision(
            proposal_id=proposal.proposal_id,
            verdict="edit_selection",
            selected_items=["missing"],
        ),
    )

    assert results == []
    assert store.calls == []


@pytest.mark.xfail(
    reason=(
        "Batch token binding + edit-selection re-approval enforcement is not implemented "
        "in BatchExecutor"
    )
)
def test_batch_edit_selection_requires_binding_and_reapproval() -> None:
    store = _BatchStore()
    executor = BatchExecutor(work_item_store=store)
    proposal = BatchProposal(
        proposal_id="proposal-1",
        action="archive",
        items=[
            BatchActionItem(item_id="i1", title="First", actor="alice"),
            BatchActionItem(item_id="i2", title="Second", actor="bob"),
        ],
    )

    decision = BatchActionDecision(
        proposal_id="different-proposal",
        verdict="edit_selection",
        selected_items=["i1"],
    )

    with pytest.raises(ValueError, match="proposal|re-approval|binding"):
        executor.execute_batch(proposal, decision)


def test_skill_loader_validate_references_blocks_script_args_escape(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "sandboxed"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "outside.py").write_text("print('outside')\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: sandboxed\n"
        "description: Skill used for sandbox path validation coverage.\n"
        "script_args:\n"
        "  ../outside.py:\n"
        "    path:\n"
        "      type: string\n"
        "---\n\n"
        "# Sandboxed\n",
        encoding="utf-8",
    )

    loader = SilasSkillLoader(skills_dir)
    report = loader.validate("sandboxed")

    assert report["valid"] is False
    assert any("escapes skill directory" in str(error) for error in report["errors"])


@pytest.mark.xfail(reason="SandboxManager backends are planned but not implemented in this codebase")
def test_subprocess_sandbox_backend_module_exists() -> None:
    from silas.execution.subprocess_sandbox import SubprocessSandboxManager  # noqa: PLC0415

    assert SubprocessSandboxManager is not None


@pytest.mark.asyncio
async def test_connection_schedule_proactive_refresh_ttl_transitions(tmp_path: Path) -> None:
    manager = SilasConnectionManager(skills_dir=tmp_path / "skills")
    now = datetime.now(timezone.utc)
    connection_id = await manager.activate_connection(
        skill_name="github",
        provider="GitHub",
        auth_payload={"token_expires_at": (now + timedelta(minutes=5)).isoformat()},
    )

    await manager.schedule_proactive_refresh(connection_id)
    assert connection_id in manager.scheduled_refreshes

    await manager.schedule_proactive_refresh(
        connection_id,
        HealthCheckResult(healthy=True, token_expires_at=now + timedelta(minutes=30)),
    )
    assert connection_id not in manager.scheduled_refreshes

    await manager.schedule_proactive_refresh(
        connection_id,
        HealthCheckResult(healthy=True, token_expires_at=None),
    )
    assert connection_id not in manager.scheduled_refreshes


@pytest.mark.asyncio
async def test_connection_health_checks_skip_non_active_connections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_dir = tmp_path / "skills"
    _touch_script(skills_dir, "github", "health_check.py")

    manager = SilasConnectionManager(skills_dir=skills_dir)
    active_id = await manager.activate_connection(
        skill_name="github",
        provider="GitHub",
        auth_payload={},
    )
    inactive_id = await manager.activate_connection(
        skill_name="github",
        provider="GitHub",
        auth_payload={"status": "inactive"},
    )

    process = _RequestResponseProcess(
        stdout_lines=[json.dumps({"healthy": True, "latency_ms": 12, "warnings": []})]
    )
    calls = {"count": 0}

    async def _fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN002, ANN003
        del args, kwargs
        calls["count"] += 1
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    results = await manager.run_health_checks()
    connections = {conn.connection_id: conn for conn in await manager.list_connections()}

    assert len(results) == 1
    assert calls["count"] == 1
    assert connections[active_id].status == "active"
    assert connections[inactive_id].status == "inactive"


@pytest.mark.asyncio
async def test_connection_refresh_token_failure_marks_error_and_clears_schedule(
    tmp_path: Path,
) -> None:
    manager = SilasConnectionManager(skills_dir=tmp_path / "skills")
    now = datetime.now(timezone.utc)
    connection_id = await manager.activate_connection(
        skill_name="github",
        provider="GitHub",
        auth_payload={"token_expires_at": (now + timedelta(minutes=2)).isoformat()},
    )
    manager._scheduled_refresh.add(connection_id)  # noqa: SLF001

    refreshed = await manager.refresh_token(connection_id)
    connections = {conn.connection_id: conn for conn in await manager.list_connections()}

    assert refreshed is False
    assert connections[connection_id].status == "error"
    assert connection_id not in manager.scheduled_refreshes


@pytest.mark.asyncio
async def test_connection_recover_unknown_connection_returns_not_found(tmp_path: Path) -> None:
    manager = SilasConnectionManager(skills_dir=tmp_path / "skills")

    success, message = await manager.recover("missing")

    assert success is False
    assert message == "connection not found"
