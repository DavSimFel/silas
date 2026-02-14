from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from silas.gates import PredicateChecker, SilasAccessController, SilasGateRunner
from silas.models.gates import (
    AccessLevel,
    Gate,
    GateLane,
    GateProvider,
    GateResult,
    GateTrigger,
    GateType,
)
from silas.models.work import WorkItem, WorkItemType


class StaticGateProvider:
    def __init__(self, result: GateResult) -> None:
        self._result = result

    async def check(self, gate: Gate, context: dict[str, object]) -> GateResult:
        del gate, context
        return self._result.model_copy(deep=True)


def _gate(
    name: str,
    *,
    on: GateTrigger = GateTrigger.every_user_message,
    provider: GateProvider = GateProvider.predicate,
    gate_type: GateType = GateType.string_match,
    check: str | None = None,
    config: dict[str, object] | None = None,
    after_step: int | None = None,
) -> Gate:
    return Gate(
        name=name,
        on=on,
        after_step=after_step,
        provider=provider,
        type=gate_type,
        check=check,
        config=config or {},
    )


@pytest.mark.asyncio
async def test_policy_gate_blocks_content() -> None:
    runner = SilasGateRunner(predicate_checker=PredicateChecker())
    gate = _gate(
        "no_forbidden",
        gate_type=GateType.regex,
        config={"pattern": r"^(?!.*forbidden).*$"},
    )

    policy_results, quality_results, merged_context = await runner.check_gates(
        gates=[gate],
        trigger=GateTrigger.every_user_message,
        context={"message": "this contains forbidden text"},
    )

    assert len(policy_results) == 1
    assert policy_results[0].action == "block"
    assert quality_results == []
    assert merged_context["message"] == "this contains forbidden text"


@pytest.mark.asyncio
async def test_quality_gate_logs_but_passes() -> None:
    runner = SilasGateRunner(
        providers={
            GateProvider.llm: StaticGateProvider(
                GateResult(
                    gate_name="quality_guard",
                    lane=GateLane.policy,
                    action="block",
                    reason="quality checker thought this was low quality",
                )
            )
        }
    )
    gate = _gate("quality_guard", provider=GateProvider.llm, check="off_topic")

    policy_results, quality_results, _ = await runner.check_gates(
        gates=[gate],
        trigger=GateTrigger.every_user_message,
        context={"message": "hello"},
    )

    assert policy_results == []
    assert len(quality_results) == 1
    assert quality_results[0].lane == GateLane.quality
    assert quality_results[0].action == "continue"
    assert "quality_lane_violation" in quality_results[0].flags
    assert len(runner.quality_log) == 1


@pytest.mark.asyncio
async def test_predicate_regex_evaluation() -> None:
    runner = SilasGateRunner(predicate_checker=PredicateChecker())
    gate = _gate(
        "match_ticket",
        gate_type=GateType.regex,
        config={"pattern": r"TICKET-\d+"},
    )

    policy_results, _, _ = await runner.check_gates(
        gates=[gate],
        trigger=GateTrigger.every_user_message,
        context={"message": "Please review TICKET-42 today."},
    )

    assert policy_results[0].action == "continue"


@pytest.mark.asyncio
async def test_predicate_length_evaluation() -> None:
    runner = SilasGateRunner(predicate_checker=PredicateChecker())
    gate = _gate(
        "message_length",
        check="length",
        config={"max_chars": 8},
    )

    policy_results, _, _ = await runner.check_gates(
        gates=[gate],
        trigger=GateTrigger.every_user_message,
        context={"message": "this is definitely too long"},
    )

    assert policy_results[0].action == "block"
    assert "max_chars" in policy_results[0].reason


@pytest.mark.asyncio
async def test_predicate_keyword_blocking() -> None:
    runner = SilasGateRunner(predicate_checker=PredicateChecker())
    gate = _gate(
        "keyword_policy",
        check="keyword",
        config={"blocked_keywords": ["secret"]},
    )

    policy_results, _, _ = await runner.check_gates(
        gates=[gate],
        trigger=GateTrigger.every_user_message,
        context={"message": "The secret code is 1234"},
    )

    assert policy_results[0].action == "block"


@pytest.mark.asyncio
async def test_predicate_keyword_require_approval() -> None:
    runner = SilasGateRunner(predicate_checker=PredicateChecker())
    gate = _gate(
        "must_include_invoice",
        check="keyword",
        config={"required_keywords": ["invoice"]},
    )

    policy_results, _, _ = await runner.check_gates(
        gates=[gate],
        trigger=GateTrigger.every_user_message,
        context={"message": "Need help with my account"},
    )

    assert policy_results[0].action == "require_approval"


@pytest.mark.asyncio
async def test_predicate_composable_or_logic() -> None:
    runner = SilasGateRunner(predicate_checker=PredicateChecker())
    gate = _gate(
        "composed",
        config={
            "logic": "or",
            "predicates": [
                {"type": "regex", "pattern": r"^denied$"},
                {"type": "keyword", "required_keywords": ["allow"]},
            ],
        },
    )

    policy_results, _, _ = await runner.check_gates(
        gates=[gate],
        trigger=GateTrigger.every_user_message,
        context={"message": "please allow this request"},
    )

    assert policy_results[0].action == "continue"


def test_access_level_filtering() -> None:
    levels = {
        "anonymous": AccessLevel(description="Anon", tools=["read"]),
        "authenticated": AccessLevel(
            description="Auth",
            tools=["read", "search"],
            requires=["gate_auth"],
        ),
        "trusted": AccessLevel(
            description="Trusted",
            tools=["read", "search", "write"],
            requires=["gate_auth", "gate_trust"],
        ),
        "owner": AccessLevel(description="Owner", tools=["*"]),
    }
    controller = SilasAccessController(owner_id="owner", access_levels=levels)
    all_tools = ["read", "search", "write"]

    assert controller.filter_tools("conn-1", all_tools) == ["read"]
    controller.gate_passed("conn-1", "gate_auth")
    assert controller.filter_tools("conn-1", all_tools) == ["read", "search"]
    controller.gate_passed("conn-1", "gate_trust")
    assert controller.filter_tools("conn-1", all_tools) == ["read", "search", "write"]


def test_owner_bypass() -> None:
    controller = SilasAccessController(owner_id="owner")
    all_tools = ["read", "search", "write"]

    assert controller.get_access_level("owner") == "owner"
    assert controller.filter_tools("owner", all_tools) == all_tools
    assert controller.gate_passed("owner", "any_gate") == "owner"


def test_access_level_expiry_downgrades_automatically() -> None:
    levels = {
        "anonymous": AccessLevel(description="Anon", tools=["read"]),
        "authenticated": AccessLevel(
            description="Auth",
            tools=["read", "search"],
            requires=["gate_auth"],
            expires_after=1,
        ),
        "trusted": AccessLevel(
            description="Trusted", tools=["read", "search", "write"], requires=[]
        ),
        "owner": AccessLevel(description="Owner", tools=["*"]),
    }
    controller = SilasAccessController(owner_id="owner", access_levels=levels)
    controller.gate_passed("conn-expiring", "gate_auth")

    state = controller._state_by_connection["conn-expiring"]
    state.level_name = "authenticated"
    state.granted_at = datetime.now(UTC) - timedelta(seconds=5)

    assert controller.get_access_level("conn-expiring") == "anonymous"
    assert controller.get_allowed_tools("conn-expiring") == ["read"]


def test_gate_precompilation_merges_config_and_work_item_gates() -> None:
    runner = SilasGateRunner(predicate_checker=PredicateChecker())
    system_gate = _gate("system_guard")
    work_gate = _gate("work_guard")
    work_item = WorkItem(
        id="w1",
        type=WorkItemType.task,
        title="Demo",
        body="Run task",
        gates=[work_gate],
    )

    compiled = runner.precompile_turn_gates(system_gates=[system_gate], work_item=work_item)
    assert [gate.name for gate in compiled] == ["system_guard", "work_guard"]

    system_gate.name = "mutated"
    assert compiled[0].name == "system_guard"


@pytest.mark.asyncio
async def test_allowed_mutations_enforced() -> None:
    mutation_result = GateResult(
        gate_name="mutate_guard",
        lane=GateLane.policy,
        action="continue",
        reason="rewrite",
        modified_context={
            "response": "safe response",
            "tool_args": {"limit": 5},
            "disallowed_key": "drop me",
        },
    )
    runner = SilasGateRunner(
        providers={GateProvider.custom: StaticGateProvider(mutation_result)},
    )
    gate = _gate("mutate_guard", provider=GateProvider.custom)

    policy_results, _, merged_context = await runner.check_gates(
        gates=[gate],
        trigger=GateTrigger.every_user_message,
        context={"message": "hello", "tool_args": {"mode": "strict"}},
    )

    assert policy_results[0].modified_context == {
        "response": "safe response",
        "tool_args": {"limit": 5},
    }
    assert merged_context["response"] == "safe response"
    assert merged_context["tool_args"] == {"mode": "strict", "limit": 5}
    assert "disallowed_key" not in merged_context
    assert ("mutate_guard", "disallowed_key") in runner.rejected_mutations


@pytest.mark.asyncio
async def test_mid_execution_after_step_gate_runs_only_on_matching_step() -> None:
    runner = SilasGateRunner(predicate_checker=PredicateChecker())
    first_step_gate = _gate(
        "step_one",
        on=GateTrigger.after_step,
        after_step=1,
        gate_type=GateType.regex,
        config={"pattern": "ok"},
    )
    second_step_gate = _gate(
        "step_two",
        on=GateTrigger.after_step,
        after_step=2,
        gate_type=GateType.regex,
        config={"pattern": "ok"},
    )

    policy_results, _, _ = await runner.check_after_step(
        gates=[first_step_gate, second_step_gate],
        step_index=2,
        context={"step_output": "ok"},
    )

    assert len(policy_results) == 1
    assert policy_results[0].gate_name == "step_two"
