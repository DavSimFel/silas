from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from silas.models.approval import ApprovalDecision, ApprovalScope, ApprovalToken, ApprovalVerdict
from silas.models.execution import (
    ExecutionEnvelope,
    ExecutionResult,
    VerificationReport,
    VerificationResult,
)
from silas.models.gates import Gate, GateProvider, GateTrigger, GateType
from silas.models.skills import SkillDefinition
from silas.models.work import (
    Budget,
    Expectation,
    VerificationCheck,
    WorkItem,
    WorkItemExecutorType,
    WorkItemStatus,
    WorkItemType,
)
from silas.skills.executor import SkillExecutor
from silas.skills.registry import SkillRegistry
from silas.execution.work_executor import LiveWorkItemExecutor

from tests.fakes import InMemoryWorkItemStore


def _register_skill(registry: SkillRegistry, name: str) -> None:
    registry.register(
        SkillDefinition(
            name=name,
            description=f"{name} test skill",
            version="1.0.0",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            requires_approval=False,
            max_retries=0,
            timeout_seconds=5,
        )
    )


def _work_item(
    item_id: str,
    *,
    title: str | None = None,
    body: str | None = None,
    skills: list[str] | None = None,
    executor_type: WorkItemExecutorType = WorkItemExecutorType.skill,
    depends_on: list[str] | None = None,
    verify: list[VerificationCheck] | None = None,
    gates: list[Gate] | None = None,
    status: WorkItemStatus = WorkItemStatus.pending,
    budget: Budget | None = None,
    include_approval: bool = True,
) -> WorkItem:
    work_item = WorkItem(
        id=item_id,
        type=WorkItemType.task,
        title=title or item_id,
        body=body or f"Execute {item_id}",
        skills=skills or [],
        executor_type=executor_type,
        depends_on=depends_on or [],
        verify=verify or [],
        gates=gates or [],
        status=status,
        budget=budget or Budget(),
    )
    if include_approval:
        work_item.approval_token = _approval_token_for(work_item)
    return work_item


def _approval_token_for(work_item: WorkItem) -> ApprovalToken:
    now = datetime.now(UTC)
    return ApprovalToken(
        token_id=f"tok:{work_item.id}",
        plan_hash=work_item.plan_hash(),
        work_item_id=work_item.id,
        scope=ApprovalScope.full_plan,
        verdict=ApprovalVerdict.approved,
        signature=b"test-signature",
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=30),
        nonce=f"nonce:{work_item.id}",
        executions_used=1,
        max_executions=1,
    )


class _StubApprovalVerifier:
    def __init__(self, *, valid: bool = True, reason: str = "ok") -> None:
        self._valid = valid
        self._reason = reason
        self.check_calls: list[tuple[str, str]] = []

    async def issue_token(
        self,
        work_item: WorkItem,
        decision: ApprovalDecision,
        scope: ApprovalScope = ApprovalScope.full_plan,
    ) -> ApprovalToken:
        del decision, scope
        return _approval_token_for(work_item)

    async def verify(
        self,
        token: ApprovalToken,
        work_item: WorkItem,
        spawned_task: WorkItem | None = None,
    ) -> tuple[bool, str]:
        del token, work_item, spawned_task
        return self._valid, self._reason

    async def check(self, token: ApprovalToken, work_item: WorkItem) -> tuple[bool, str]:
        self.check_calls.append((token.token_id, work_item.id))
        return self._valid, self._reason


class _StubVerificationRunner:
    def __init__(
        self, *, all_passed: bool = True, fail_reason: str = "verification failed"
    ) -> None:
        self._all_passed = all_passed
        self._fail_reason = fail_reason
        self.run_calls: list[list[VerificationCheck]] = []

    async def run_checks(self, checks: list[VerificationCheck]) -> VerificationReport:
        self.run_calls.append([check.model_copy(deep=True) for check in checks])
        results = [
            VerificationResult(
                name=check.name,
                passed=self._all_passed,
                reason="passed" if self._all_passed else self._fail_reason,
            )
            for check in checks
        ]
        failed = [result for result in results if not result.passed]
        return VerificationReport(
            all_passed=not failed,
            results=results,
            failed=failed,
        )


class _StubEphemeralExecutor:
    def __init__(self, *, success: bool = True, error: str | None = None) -> None:
        self._success = success
        self._error = error
        self.calls: list[ExecutionEnvelope] = []

    async def execute(self, envelope: ExecutionEnvelope) -> ExecutionResult:
        self.calls.append(envelope.model_copy(deep=True))
        return ExecutionResult(
            execution_id=envelope.execution_id,
            step_index=envelope.step_index,
            success=self._success,
            return_value="ok" if self._success else "",
            error=self._error,
            duration_seconds=0.05,
        )


class _StubConsultPlannerManager:
    def __init__(self, guidance: str | None = None) -> None:
        self._guidance = guidance
        self.calls: list[tuple[str, str, str]] = []

    async def consult(
        self,
        work_item_id: str,
        failure_context: str,
        trace_id: str,
        timeout_s: float = 90.0,
    ) -> str | None:
        del timeout_s
        self.calls.append((work_item_id, failure_context, trace_id))
        return self._guidance


class _StubReplanManager:
    def __init__(self, *, enqueued: bool = True) -> None:
        self._enqueued = enqueued
        self.calls: list[dict[str, object]] = []

    async def trigger_replan(
        self,
        work_item_id: str,
        original_goal: str,
        failure_history: list[dict[str, object]],
        trace_id: str,
        current_depth: int = 0,
    ) -> bool:
        self.calls.append(
            {
                "work_item_id": work_item_id,
                "original_goal": original_goal,
                "failure_history": failure_history,
                "trace_id": trace_id,
                "current_depth": current_depth,
            }
        )
        return self._enqueued


class _StubGateRunner:
    def __init__(
        self, *, block_on_tool_call: bool = False, block_after_step: int | None = None
    ) -> None:
        self._block_on_tool_call = block_on_tool_call
        self._block_after_step = block_after_step
        self.on_tool_call_checks: list[dict[str, object]] = []
        self.after_step_checks: list[tuple[int, dict[str, object]]] = []

    async def check_gates(
        self,
        gates: list[Gate],
        trigger: GateTrigger,
        context: dict[str, object],
    ) -> tuple[list[object], list[object], dict[str, object]]:
        del gates
        if trigger == GateTrigger.on_tool_call:
            self.on_tool_call_checks.append(dict(context))
            if self._block_on_tool_call:
                return (
                    [
                        type(
                            "_GateResult",
                            (),
                            {"action": "block", "gate_name": "tool-call", "reason": "blocked"},
                        )()
                    ],
                    [],
                    context,
                )
        return ([], [], context)

    async def check_after_step(
        self,
        gates: list[Gate],
        step_index: int,
        context: dict[str, object],
    ) -> tuple[list[object], list[object], dict[str, object]]:
        del gates
        self.after_step_checks.append((step_index, dict(context)))
        if self._block_after_step is not None and step_index >= self._block_after_step:
            return (
                [
                    type(
                        "_GateResult",
                        (),
                        {"action": "block", "gate_name": "after-step", "reason": "blocked"},
                    )()
                ],
                [],
                context,
            )
        return ([], [], context)


@pytest.fixture
def work_store() -> InMemoryWorkItemStore:
    return InMemoryWorkItemStore()


@pytest.fixture
def skill_registry() -> SkillRegistry:
    return SkillRegistry()


@pytest.fixture
def skill_executor(skill_registry: SkillRegistry) -> SkillExecutor:
    return SkillExecutor(skill_registry=skill_registry)


@pytest.fixture
def approval_verifier() -> _StubApprovalVerifier:
    return _StubApprovalVerifier(valid=True, reason="ok")


@pytest.fixture
def verification_runner() -> _StubVerificationRunner:
    return _StubVerificationRunner(all_passed=True)


@pytest.fixture
def work_executor(
    work_store: InMemoryWorkItemStore,
    skill_executor: SkillExecutor,
    approval_verifier: _StubApprovalVerifier,
    verification_runner: _StubVerificationRunner,
) -> LiveWorkItemExecutor:
    return LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=approval_verifier,
        verification_runner=verification_runner,
    )


@pytest.mark.asyncio
async def test_single_task_execution(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "skill_a")
    calls: list[str] = []

    async def _handler(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _handler)

    result = await work_executor.execute(_work_item("task-a", skills=["skill_a"]))

    assert result.status == WorkItemStatus.done
    assert calls == ["task-a"]
    loaded = await work_store.get("task-a")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.done


@pytest.mark.asyncio
async def test_dependency_ordering_runs_dependencies_first(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "skill_a")
    _register_skill(skill_registry, "skill_b")
    calls: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    async def _skill_b(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    skill_executor.register_handler("skill_b", _skill_b)

    dep = _work_item("task-a", skills=["skill_a"])
    root = _work_item("task-b", skills=["skill_b"], depends_on=["task-a"])
    await work_store.save(dep)

    result = await work_executor.execute(root)

    assert result.status == WorkItemStatus.done
    assert calls == ["task-a", "task-b"]
    loaded_dep = await work_store.get("task-a")
    loaded_root = await work_store.get("task-b")
    assert loaded_dep is not None
    assert loaded_dep.status == WorkItemStatus.done
    assert loaded_root is not None
    assert loaded_root.status == WorkItemStatus.done


@pytest.mark.asyncio
async def test_circular_dependency_detection_sets_failed_status(
    work_executor: LiveWorkItemExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    item_a = _work_item("task-a", depends_on=["task-b"])
    item_b = _work_item("task-b", depends_on=["task-a"])
    await work_store.save(item_b)

    result = await work_executor.execute(item_a)

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "circular dependency" in result.last_error
    loaded = await work_store.get("task-a")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed


@pytest.mark.asyncio
async def test_retry_on_failure_then_success(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "flaky")
    attempts = {"count": 0}

    async def _flaky(_inputs: dict[str, object]) -> dict[str, object]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("boom")
        return {"ok": True}

    skill_executor.register_handler("flaky", _flaky)

    item = _work_item(
        "task-retry",
        skills=["flaky"],
        budget=Budget(max_attempts=3),
    )
    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.done
    assert attempts["count"] == 2
    loaded = await work_store.get("task-retry")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.done
    assert loaded.attempts == 2


@pytest.mark.asyncio
async def test_retry_exhaustion_marks_failed(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "always_fail")

    async def _always_fail(_inputs: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("still failing")

    skill_executor.register_handler("always_fail", _always_fail)

    item = _work_item(
        "task-fail",
        skills=["always_fail"],
        budget=Budget(max_attempts=2),
    )
    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.failed
    assert result.last_error == "still failing"
    loaded = await work_store.get("task-fail")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed
    assert loaded.attempts == 2


@pytest.mark.asyncio
async def test_on_tool_call_gate_blocks_execution_before_attempt(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    _register_skill(skill_registry, "skill_a")
    calls: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    gate_runner = _StubGateRunner(block_on_tool_call=True)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        gate_runner=gate_runner,
    )
    gate = Gate(
        name="tool-call",
        on=GateTrigger.on_tool_call,
        provider=GateProvider.predicate,
        type=GateType.string_match,
        check="allow",
    )
    item = _work_item("task-gated-tool-call", skills=["skill_a"], gates=[gate])

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.blocked
    assert gate_runner.on_tool_call_checks
    assert calls == []


@pytest.mark.asyncio
async def test_after_step_gate_blocks_retries_between_attempts(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    _register_skill(skill_registry, "always_fail")
    calls = {"count": 0}

    async def _always_fail(_inputs: dict[str, object]) -> dict[str, object]:
        calls["count"] += 1
        raise RuntimeError("failed")

    skill_executor.register_handler("always_fail", _always_fail)
    gate_runner = _StubGateRunner(block_after_step=1)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        gate_runner=gate_runner,
    )
    gate = Gate(
        name="after-step",
        on=GateTrigger.after_step,
        after_step=1,
        provider=GateProvider.predicate,
        type=GateType.string_match,
        check="allow",
    )
    item = _work_item(
        "task-gated-after-step",
        skills=["always_fail"],
        budget=Budget(max_attempts=3),
        gates=[gate],
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.blocked
    assert calls["count"] == 1
    assert gate_runner.after_step_checks


@pytest.mark.asyncio
async def test_budget_tracking_populates_budget_used(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "token_skill")

    async def _token_skill(_inputs: dict[str, object]) -> dict[str, object]:
        return {"payload": "x" * 120}

    skill_executor.register_handler("token_skill", _token_skill)

    result = await work_executor.execute(_work_item("task-budget", skills=["token_skill"]))

    assert result.status == WorkItemStatus.done
    assert result.budget_used.tokens > 0
    assert result.budget_used.attempts == 1
    assert result.budget_used.executor_runs == 1
    loaded = await work_store.get("task-budget")
    assert loaded is not None
    assert loaded.budget_used.tokens > 0


@pytest.mark.asyncio
async def test_skill_not_found_marks_failed(
    work_executor: LiveWorkItemExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    result = await work_executor.execute(_work_item("task-missing-skill", skills=["missing_skill"]))

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "not registered" in result.last_error
    loaded = await work_store.get("task-missing-skill")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed


@pytest.mark.asyncio
async def test_missing_dependency_marks_failed(
    work_executor: LiveWorkItemExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    result = await work_executor.execute(_work_item("task-b", depends_on=["task-a"]))

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "missing dependency" in result.last_error
    loaded = await work_store.get("task-b")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed


@pytest.mark.asyncio
async def test_done_dependency_not_reexecuted(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "skill_a")
    _register_skill(skill_registry, "skill_b")
    calls: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    async def _skill_b(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    skill_executor.register_handler("skill_b", _skill_b)

    done_dep = _work_item("task-a", skills=["skill_a"], status=WorkItemStatus.done)
    await work_store.save(done_dep)

    result = await work_executor.execute(
        _work_item("task-b", skills=["skill_b"], depends_on=["task-a"])
    )

    assert result.status == WorkItemStatus.done
    assert calls == ["task-b"]


@pytest.mark.asyncio
async def test_failed_dependency_stops_downstream_execution(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "skill_a")
    _register_skill(skill_registry, "skill_b")
    calls: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        raise RuntimeError("dependency error")

    async def _skill_b(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    skill_executor.register_handler("skill_b", _skill_b)

    await work_store.save(_work_item("task-a", skills=["skill_a"], budget=Budget(max_attempts=1)))
    root = _work_item(
        "task-b", skills=["skill_b"], depends_on=["task-a"], budget=Budget(max_attempts=1)
    )

    result = await work_executor.execute(root)

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "dependency task-a failed" in result.last_error
    assert calls == ["task-a"]
    loaded_root = await work_store.get("task-b")
    assert loaded_root is not None
    assert loaded_root.status == WorkItemStatus.failed


@pytest.mark.asyncio
async def test_shell_executor_type_uses_registry_executor(
    work_store: InMemoryWorkItemStore,
    skill_executor: SkillExecutor,
) -> None:
    shell_executor = _StubEphemeralExecutor(success=True)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        executor_registry={WorkItemExecutorType.shell: shell_executor},
    )
    item = _work_item(
        "task-shell-exec",
        body='["echo", "hello"]',
        executor_type=WorkItemExecutorType.shell,
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.done
    assert len(shell_executor.calls) == 1
    call = shell_executor.calls[0]
    assert call.action == "shell_exec"
    assert call.args["command"] == ["echo", "hello"]


@pytest.mark.asyncio
async def test_python_executor_type_uses_registry_executor(
    work_store: InMemoryWorkItemStore,
    skill_executor: SkillExecutor,
) -> None:
    python_executor = _StubEphemeralExecutor(success=True)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        executor_registry={WorkItemExecutorType.python: python_executor},
    )
    item = _work_item(
        "task-python-exec",
        body="print('hello from python')",
        executor_type=WorkItemExecutorType.python,
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.done
    assert len(python_executor.calls) == 1
    call = python_executor.calls[0]
    assert call.action == "python_exec"
    assert call.args["script"] == "print('hello from python')"


@pytest.mark.asyncio
async def test_registry_executor_failure_marks_work_item_failed(
    work_store: InMemoryWorkItemStore,
    skill_executor: SkillExecutor,
) -> None:
    shell_executor = _StubEphemeralExecutor(success=False, error="nonzero exit")
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        executor_registry={WorkItemExecutorType.shell: shell_executor},
    )
    item = _work_item(
        "task-shell-fail",
        body='["false"]',
        executor_type=WorkItemExecutorType.shell,
        budget=Budget(max_attempts=1),
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.failed
    assert result.last_error == "nonzero exit"


@pytest.mark.asyncio
async def test_missing_approval_token_blocks_execution(
    work_executor: LiveWorkItemExecutor,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "skill_a")
    calls: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    item = _work_item("task-no-token", skills=["skill_a"], include_approval=False)

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.blocked
    assert result.last_error is not None
    assert "missing approval token" in result.last_error
    assert calls == []
    loaded = await work_store.get("task-no-token")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.blocked


@pytest.mark.asyncio
async def test_invalid_approval_token_blocks_execution(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    _register_skill(skill_registry, "skill_a")
    verifier = _StubApprovalVerifier(valid=False, reason="invalid_signature")
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=verifier,
    )

    calls: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    item = _work_item("task-invalid-token", skills=["skill_a"])

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.blocked
    assert result.last_error is not None
    assert "invalid_signature" in result.last_error
    assert verifier.check_calls == [(f"tok:{item.id}", item.id)]
    assert calls == []
    loaded = await work_store.get("task-invalid-token")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.blocked


@pytest.mark.asyncio
async def test_verify_checks_must_pass_before_done(
    work_executor: LiveWorkItemExecutor,
    verification_runner: _StubVerificationRunner,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
    work_store: InMemoryWorkItemStore,
) -> None:
    _register_skill(skill_registry, "skill_a")

    async def _skill_a(_inputs: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    checks = [
        VerificationCheck(
            name="smoke",
            run="echo ok",
            expect=Expectation(contains="ok"),
        )
    ]
    item = _work_item("task-verify-pass", skills=["skill_a"], verify=checks)

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.done
    assert len(verification_runner.run_calls) == 1
    assert result.verification_results
    loaded = await work_store.get("task-verify-pass")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.done
    assert loaded.verification_results


@pytest.mark.asyncio
async def test_verification_failure_retries_and_marks_failed(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    _register_skill(skill_registry, "skill_a")
    verification_runner = _StubVerificationRunner(
        all_passed=False,
        fail_reason="expected marker missing",
    )
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        verification_runner=verification_runner,
    )
    calls: list[str] = []

    async def _skill_a(inputs: dict[str, object]) -> dict[str, object]:
        calls.append(str(inputs["work_item_id"]))
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    checks = [
        VerificationCheck(
            name="smoke",
            run="echo mismatch",
            expect=Expectation(contains="ok"),
        )
    ]
    item = _work_item(
        "task-verify-fail",
        skills=["skill_a"],
        verify=checks,
        budget=Budget(max_attempts=2),
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "verification failed" in result.last_error
    assert "expected marker missing" in result.last_error
    assert len(calls) == 2
    assert len(verification_runner.run_calls) == 2
    loaded = await work_store.get("task-verify-fail")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed
    assert loaded.attempts == 2
    assert loaded.verification_results


@pytest.mark.asyncio
async def test_verify_checks_fail_when_runner_unavailable(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    _register_skill(skill_registry, "skill_a")
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
    )

    async def _skill_a(_inputs: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    checks = [
        VerificationCheck(
            name="smoke",
            run="echo ok",
            expect=Expectation(contains="ok"),
        )
    ]
    item = _work_item(
        "task-verify-no-runner",
        skills=["skill_a"],
        verify=checks,
        budget=Budget(max_attempts=1),
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.failed
    assert result.last_error is not None
    assert "verification runner unavailable" in result.last_error
    loaded = await work_store.get("task-verify-no-runner")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed


@pytest.mark.asyncio
async def test_on_stuck_consult_planner_triggers_consult_and_replan(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    _register_skill(skill_registry, "skill_a")
    verification_runner = _StubVerificationRunner(
        all_passed=False,
        fail_reason="expected marker missing",
    )
    consult_manager = _StubConsultPlannerManager(guidance=None)
    replan_manager = _StubReplanManager(enqueued=True)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        verification_runner=verification_runner,
        consult_manager=consult_manager,
        replan_manager=replan_manager,
    )

    async def _skill_a(_inputs: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    item = _work_item(
        "task-verify-stuck",
        skills=["skill_a"],
        verify=[
            VerificationCheck(
                name="smoke",
                run="echo mismatch",
                expect=Expectation(contains="ok"),
            )
        ],
        budget=Budget(max_attempts=1),
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.stuck
    assert consult_manager.calls
    assert replan_manager.calls
    loaded = await work_store.get("task-verify-stuck")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.stuck


@pytest.mark.asyncio
async def test_on_stuck_non_consult_does_not_trigger_recovery_cascade(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    _register_skill(skill_registry, "skill_a")
    verification_runner = _StubVerificationRunner(
        all_passed=False,
        fail_reason="expected marker missing",
    )
    consult_manager = _StubConsultPlannerManager(guidance="any")
    replan_manager = _StubReplanManager(enqueued=True)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        verification_runner=verification_runner,
        consult_manager=consult_manager,
        replan_manager=replan_manager,
    )

    async def _skill_a(_inputs: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    skill_executor.register_handler("skill_a", _skill_a)
    item = _work_item(
        "task-no-cascade",
        skills=["skill_a"],
        verify=[
            VerificationCheck(
                name="smoke",
                run="echo mismatch",
                expect=Expectation(contains="ok"),
            )
        ],
        budget=Budget(max_attempts=1),
    ).model_copy(update={"on_stuck": "report"})

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.failed
    assert consult_manager.calls == []
    assert replan_manager.calls == []


# ── Consult/Replan Cascade: LiveWorkItemExecutor Path ──────────────


class _StubAuditLog:
    """Audit log that records events for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def log(self, event: str, **kwargs: object) -> None:
        self.events.append((event, dict(kwargs)))

    def event_names(self) -> list[str]:
        return [name for name, _ in self.events]


class _VariableConsultManager:
    """ConsultPlannerManager stub that returns different guidance per call."""

    def __init__(self, responses: list[str | None]) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: list[tuple[str, str, str]] = []

    async def consult(
        self,
        work_item_id: str,
        failure_context: str,
        trace_id: str,
        timeout_s: float = 90.0,
    ) -> str | None:
        del timeout_s
        self.calls.append((work_item_id, failure_context, trace_id))
        if self._index < len(self._responses):
            result = self._responses[self._index]
            self._index += 1
            return result
        return None


class _CountingSkillExecutor(SkillExecutor):
    """SkillExecutor that fails N times then succeeds."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        fail_count: int = 0,
    ) -> None:
        super().__init__(skill_registry=registry)
        self._fail_count = fail_count
        self._call_count = 0

    async def execute(
        self,
        skill_name: str,
        inputs: dict[str, object],
    ) -> object:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            # Return a failure-like result
            return type(
                "_FailResult",
                (),
                {
                    "success": False,
                    "error": f"attempt {self._call_count} failed",
                    "output": "",
                    "duration_ms": 10.0,
                },
            )()
        return type(
            "_OkResult",
            (),
            {"success": True, "error": None, "output": "done", "duration_ms": 10.0},
        )()

    @property
    def call_count(self) -> int:
        return self._call_count


@pytest.mark.asyncio
async def test_consult_guidance_enables_guided_retry_success(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    """Consult returns guidance → guided retry succeeds → done."""
    _register_skill(skill_registry, "flaky")
    attempts = {"count": 0}

    async def _flaky(_inputs: dict[str, object]) -> dict[str, object]:
        attempts["count"] += 1
        # Fail on first attempt (normal retry loop exhausts with 1 attempt),
        # succeed on second (guided retry from cascade).
        if attempts["count"] <= 1:
            raise RuntimeError("transient failure")
        return {"ok": True}

    skill_executor.register_handler("flaky", _flaky)

    consult_manager = _StubConsultPlannerManager(guidance="Try a different strategy")
    replan_manager = _StubReplanManager(enqueued=True)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        consult_manager=consult_manager,
        replan_manager=replan_manager,
    )

    # max_attempts=1: retry loop exhausts after 1 failure, cascade
    # consults planner → guided retry succeeds (attempt #2).
    item = _work_item(
        "task-guided-success",
        skills=["flaky"],
        budget=Budget(max_attempts=1),
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.done
    # execute() wraps single-item results; verify the cascade path ran
    assert attempts["count"] == 2  # 1 normal + 1 guided retry
    # Consult was called once
    assert len(consult_manager.calls) == 1
    # Replan was NOT triggered (guided retry succeeded)
    assert replan_manager.calls == []


@pytest.mark.asyncio
async def test_guided_retry_fails_triggers_replan(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    """Consult returns guidance → guided retry fails → replan triggered → stuck."""
    _register_skill(skill_registry, "always_fail")

    async def _always_fail(_inputs: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("persistent failure")

    skill_executor.register_handler("always_fail", _always_fail)

    consult_manager = _StubConsultPlannerManager(guidance="Try XYZ approach")
    replan_manager = _StubReplanManager(enqueued=True)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        consult_manager=consult_manager,
        replan_manager=replan_manager,
    )

    item = _work_item(
        "task-guided-fail-replan",
        skills=["always_fail"],
        budget=Budget(max_attempts=1),
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.stuck
    assert len(consult_manager.calls) == 1
    assert len(replan_manager.calls) == 1
    loaded = await work_store.get("task-guided-fail-replan")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.stuck


@pytest.mark.asyncio
async def test_replan_not_enqueued_marks_failed(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    """Replan manager returns False (max depth) → failed with recovery exhausted."""
    _register_skill(skill_registry, "always_fail")

    async def _always_fail(_inputs: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("persistent failure")

    skill_executor.register_handler("always_fail", _always_fail)

    consult_manager = _StubConsultPlannerManager(guidance=None)
    replan_manager = _StubReplanManager(enqueued=False)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        consult_manager=consult_manager,
        replan_manager=replan_manager,
    )

    item = _work_item(
        "task-replan-exhausted",
        skills=["always_fail"],
        budget=Budget(max_attempts=1),
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.failed
    assert "recovery exhausted" in result.summary.lower()
    loaded = await work_store.get("task-replan-exhausted")
    assert loaded is not None
    assert loaded.status == WorkItemStatus.failed


@pytest.mark.asyncio
async def test_cascade_audit_events_logged(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    """Verify audit events are emitted during the cascade."""
    _register_skill(skill_registry, "always_fail")

    async def _always_fail(_inputs: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("boom")

    skill_executor.register_handler("always_fail", _always_fail)

    audit = _StubAuditLog()
    consult_manager = _StubConsultPlannerManager(guidance=None)
    replan_manager = _StubReplanManager(enqueued=True)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        audit=audit,
        consult_manager=consult_manager,
        replan_manager=replan_manager,
    )

    item = _work_item(
        "task-audit-cascade",
        skills=["always_fail"],
        budget=Budget(max_attempts=1),
    )

    await work_executor.execute(item)

    event_names = audit.event_names()
    # Consult timed out → audit event
    assert "consult_planner_timeout" in event_names
    # Replan was triggered → audit event
    assert "replan_triggered" in event_names


@pytest.mark.asyncio
async def test_planner_budget_exhausted_audit_event(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    """Verify audit event fires when planner call budget is exhausted."""
    _register_skill(skill_registry, "always_fail")

    async def _always_fail(_inputs: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("boom")

    skill_executor.register_handler("always_fail", _always_fail)

    audit = _StubAuditLog()
    consult_manager = _StubConsultPlannerManager(guidance=None)
    replan_manager = _StubReplanManager(enqueued=True)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        audit=audit,
        consult_manager=consult_manager,
        replan_manager=replan_manager,
    )

    item = _work_item(
        "task-budget-exhaust",
        skills=["always_fail"],
        budget=Budget(max_attempts=3, max_planner_calls=0),
    )

    await work_executor.execute(item)

    event_names = audit.event_names()
    assert "consult_planner_budget_exhausted" in event_names


@pytest.mark.asyncio
async def test_consult_charges_plan_budget_not_work_item_budget(
    work_store: InMemoryWorkItemStore,
    skill_registry: SkillRegistry,
    skill_executor: SkillExecutor,
) -> None:
    """Planner consult usage must be tracked on the plan aggregate budget."""
    _register_skill(skill_registry, "always_fail")

    async def _always_fail(_inputs: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("boom")

    skill_executor.register_handler("always_fail", _always_fail)

    consult_manager = _StubConsultPlannerManager(guidance=None)
    replan_manager = _StubReplanManager(enqueued=True)
    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_store,
        approval_verifier=_StubApprovalVerifier(valid=True, reason="ok"),
        consult_manager=consult_manager,
        replan_manager=replan_manager,
    )

    item = _work_item(
        "task-plan-budget-only",
        skills=["always_fail"],
        budget=Budget(max_attempts=1, max_planner_calls=2),
    )

    result = await work_executor.execute(item)

    assert result.status == WorkItemStatus.stuck
    assert result.budget_used.planner_calls == 1
    loaded = await work_store.get("task-plan-budget-only")
    assert loaded is not None
    assert loaded.budget_used.planner_calls == 0
