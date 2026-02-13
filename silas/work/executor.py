from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError

from silas.execution.python_exec import PythonExecutor
from silas.execution.sandbox_factory import create_sandbox_manager
from silas.execution.shell import ShellExecutor
from silas.models.execution import (
    ExecutionEnvelope,
    ExecutionResult,
    SandboxConfig,
    VerificationReport,
)
from silas.models.work import (
    BudgetUsed,
    WorkItem,
    WorkItemExecutorType,
    WorkItemResult,
    WorkItemStatus,
)
from silas.protocols.approval import ApprovalVerifier
from silas.protocols.audit import AuditLog
from silas.protocols.execution import EphemeralExecutor
from silas.protocols.work import VerificationRunner, WorkItemStore
from silas.skills.executor import SkillExecutor

if TYPE_CHECKING:
    from silas.queue.consult import ConsultPlannerManager
    from silas.queue.replan import ReplanManager

_CHARS_PER_TOKEN = 3.5

logger = logging.getLogger(__name__)

_EXECUTION_ACTIONS: dict[WorkItemExecutorType, str] = {
    WorkItemExecutorType.shell: "shell_exec",
    WorkItemExecutorType.python: "python_exec",
}


def work_item_from_execution_payload(payload: dict[str, object]) -> WorkItem | None:
    """Extract and validate a serialized WorkItem from an execution payload.

    Returns ``None`` when no work-item envelope is present.
    Raises ``ValueError`` when a work-item envelope exists but is invalid.
    """
    raw_work_item = payload.get("work_item")
    if raw_work_item is None:
        return None
    if not isinstance(raw_work_item, dict):
        raise ValueError("execution_request payload.work_item must be an object")

    try:
        return WorkItem.model_validate(raw_work_item)
    except ValidationError as exc:
        raise ValueError("invalid execution_request work_item payload") from exc


class LiveWorkItemExecutor:
    def __init__(
        self,
        skill_executor: SkillExecutor,
        work_item_store: WorkItemStore,
        *,
        shell_executor: EphemeralExecutor | None = None,
        python_executor: EphemeralExecutor | None = None,
        executor_registry: Mapping[WorkItemExecutorType, EphemeralExecutor] | None = None,
        approval_verifier: ApprovalVerifier | None = None,
        verification_runner: VerificationRunner | None = None,
        audit: AuditLog | None = None,
        consult_manager: ConsultPlannerManager | None = None,
        replan_manager: ReplanManager | None = None,
    ) -> None:
        self._skill_executor = skill_executor
        self._work_item_store = work_item_store
        self._approval_verifier = approval_verifier
        self._verification_runner = verification_runner
        self._audit = audit
        self._consult_manager = consult_manager
        self._replan_manager = replan_manager

        sandbox_manager = create_sandbox_manager()
        self._executor_registry: dict[WorkItemExecutorType, object] = {
            WorkItemExecutorType.skill: skill_executor,
            WorkItemExecutorType.shell: shell_executor or ShellExecutor(sandbox_manager),
            WorkItemExecutorType.python: python_executor or PythonExecutor(sandbox_manager),
        }
        if executor_registry is not None:
            self._executor_registry.update(executor_registry)

    async def execute(self, item: WorkItem) -> WorkItemResult:
        root_item = item.model_copy(deep=True)
        await self._work_item_store.save(root_item)

        try:
            items_by_id, prerequisites = await self._resolve_items(root_item)
            ordered_ids = self._topological_sort(prerequisites)
        except ValueError as exc:
            return await self._mark_failed(root_item, str(exc))

        aggregate_budget = BudgetUsed()
        execution_results: dict[str, WorkItemResult] = {}

        for work_item_id in ordered_ids:
            work_item = items_by_id[work_item_id]
            unmet = [
                dep_id
                for dep_id in prerequisites.get(work_item_id, set())
                if execution_results.get(dep_id) is None
                or execution_results[dep_id].status != WorkItemStatus.done
            ]
            if unmet:
                unmet_str = ", ".join(sorted(unmet))
                return await self._mark_failed(
                    root_item,
                    f"dependency not completed: {unmet_str}",
                    budget_used=aggregate_budget,
                )

            if work_item.status == WorkItemStatus.done:
                done_result = WorkItemResult(
                    work_item_id=work_item.id,
                    status=WorkItemStatus.done,
                    summary=f"Work item {work_item.id} already complete.",
                    budget_used=work_item.budget_used.model_copy(deep=True),
                )
                execution_results[work_item.id] = done_result
                aggregate_budget.merge(done_result.budget_used.model_copy(deep=True))
                continue

            result = await self._execute_single(work_item)
            execution_results[work_item.id] = result
            aggregate_budget.merge(result.budget_used.model_copy(deep=True))
            if result.status != WorkItemStatus.done:
                if work_item.id == root_item.id:
                    return result
                return await self._mark_failed(
                    root_item,
                    f"dependency {work_item.id} failed: {result.last_error or result.summary}",
                    budget_used=aggregate_budget,
                )

        root_result = execution_results.get(root_item.id)
        if root_result is not None:
            return root_result.model_copy(
                update={
                    "summary": f"Executed {len(ordered_ids)} work item(s) successfully.",
                    "budget_used": aggregate_budget.model_copy(deep=True),
                }
            )

        return WorkItemResult(
            work_item_id=root_item.id,
            status=WorkItemStatus.done,
            summary=f"Executed {len(ordered_ids)} work item(s) successfully.",
            budget_used=aggregate_budget,
        )

    async def _execute_single(self, item: WorkItem) -> WorkItemResult:
        work_item = item.model_copy(deep=True)
        used = work_item.budget_used.model_copy(deep=True)
        max_attempts = max(1, work_item.budget.max_attempts)
        last_error: str | None = None

        approved, approval_reason = await self._check_execution_approval(work_item)
        if not approved:
            await self._audit_execution_blocked(work_item, approval_reason)
            return await self._mark_blocked(
                work_item,
                f"execution_blocked_no_approval: {approval_reason}",
                budget_used=used,
            )

        uses_skill_executor = work_item.executor_type == WorkItemExecutorType.skill
        if uses_skill_executor:
            self._skill_executor.set_work_item(work_item)
        try:
            for _ in range(max_attempts):
                if used.exceeds(work_item.budget):
                    last_error = "budget exhausted before attempt"
                    break

                work_item.attempts += 1
                used.attempts += 1
                used.executor_runs += 1

                work_item.status = WorkItemStatus.running
                work_item.budget_used = used.model_copy(deep=True)
                await self._persist(work_item)

                attempt_body = self._build_attempt_body(work_item, previous_error=last_error)
                attempt_ok, attempt_error = await self._execute_attempt(work_item, used, attempt_body)
                if not attempt_ok:
                    last_error = attempt_error or "execution attempt failed"

                work_item.budget_used = used.model_copy(deep=True)
                if attempt_ok:
                    verification_ok, verification_results, verification_error = (
                        await self._run_external_verification(work_item)
                    )
                    work_item.verification_results = [
                        dict(result) for result in verification_results
                    ]
                    if not verification_ok:
                        last_error = verification_error or "verification failed"
                        if used.exceeds(work_item.budget):
                            break
                        continue

                    work_item.status = WorkItemStatus.done
                    await self._persist(work_item)
                    return WorkItemResult(
                        work_item_id=work_item.id,
                        status=WorkItemStatus.done,
                        summary=f"Work item {work_item.id} completed.",
                        verification_results=work_item.verification_results,
                        budget_used=used.model_copy(deep=True),
                    )

                if used.exceeds(work_item.budget):
                    last_error = last_error or "budget exhausted"
                    break

            stuck_result = await self._attempt_stuck_recovery(work_item, used, last_error)
            if stuck_result is not None:
                return stuck_result

            work_item.status = WorkItemStatus.failed
            work_item.budget_used = used.model_copy(deep=True)
            await self._persist(work_item)
            return WorkItemResult(
                work_item_id=work_item.id,
                status=WorkItemStatus.failed,
                summary=f"Work item {work_item.id} failed.",
                last_error=last_error,
                verification_results=work_item.verification_results,
                budget_used=used.model_copy(deep=True),
            )
        finally:
            if uses_skill_executor:
                self._skill_executor.set_work_item(None)

    async def _execute_attempt(
        self,
        work_item: WorkItem,
        used: BudgetUsed,
        attempt_body: str,
    ) -> tuple[bool, str | None]:
        if work_item.executor_type == WorkItemExecutorType.skill:
            return await self._execute_skill_attempt(work_item, used, attempt_body)
        return await self._execute_registered_attempt(work_item, used)

    async def _execute_skill_attempt(
        self,
        work_item: WorkItem,
        used: BudgetUsed,
        attempt_body: str,
    ) -> tuple[bool, str | None]:
        if not work_item.skills:
            attempt_started = datetime.now(UTC)
            used.tokens += self._estimate_tokens(attempt_body)
            used.wall_time_seconds += (datetime.now(UTC) - attempt_started).total_seconds()
            return True, None

        for skill_name in work_item.skills:
            skill_result = await self._skill_executor.execute(
                skill_name,
                {
                    "work_item_id": work_item.id,
                    "title": work_item.title,
                    "body": attempt_body,
                    "attempt": work_item.attempts,
                    "depends_on": work_item.depends_on,
                },
            )
            used.tokens += self._estimate_tokens(skill_result.output, skill_result.error)
            used.wall_time_seconds += skill_result.duration_ms / 1000.0
            if not skill_result.success:
                return False, skill_result.error or f"skill '{skill_name}' failed"

        return True, None

    async def _execute_registered_attempt(
        self,
        work_item: WorkItem,
        used: BudgetUsed,
    ) -> tuple[bool, str | None]:
        executor = self._resolve_executor(work_item.executor_type)
        if executor is None:
            return False, f"executor '{work_item.executor_type.value}' is not registered"

        envelope = self._build_execution_envelope(work_item)
        try:
            result = await executor.execute(envelope)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            return False, f"{work_item.executor_type.value} executor error: {exc}"

        self._merge_execution_budget(used, result)
        if result.success:
            return True, None

        error = result.error or f"{work_item.executor_type.value} execution failed"
        return False, error

    def _build_attempt_body(
        self,
        work_item: WorkItem,
        *,
        previous_error: str | None,
        planner_guidance: str | None = None,
    ) -> str:
        if work_item.executor_type != WorkItemExecutorType.skill:
            return work_item.body

        parts: list[str] = [work_item.body]
        if work_item.attempts > 1 and previous_error:
            parts.append(
                f"Previous attempt {work_item.attempts - 1} failed:\n{previous_error}",
            )
        if planner_guidance:
            parts.append(f"Planner guidance:\n{planner_guidance}")
        return "\n\n".join(parts)

    async def _attempt_stuck_recovery(
        self,
        work_item: WorkItem,
        used: BudgetUsed,
        last_error: str | None,
    ) -> WorkItemResult | None:
        if work_item.on_stuck != "consult_planner":
            return None

        failure_context = last_error or "unknown failure"
        guidance, guidance_error = await self._consult_planner(work_item, used, failure_context)
        if guidance is not None:
            # The guided retry is the recovery mechanism AFTER the normal retry
            # loop is exhausted (spec §5.2.1 step e). It bypasses the attempt
            # budget — the planner call budget (max_planner_calls) is the guard
            # for this path, not max_attempts.
            guided_result = await self._execute_guided_retry(
                work_item,
                used,
                last_error=failure_context,
                guidance=guidance,
            )
            if guided_result.status == WorkItemStatus.done:
                return guided_result
            failure_context = guided_result.last_error or failure_context
        elif guidance_error is not None:
            failure_context = f"{failure_context}; {guidance_error}"

        replan_result = await self._trigger_replan(work_item, used, failure_context)
        if replan_result is not None:
            return replan_result
        return None

    async def _execute_guided_retry(
        self,
        work_item: WorkItem,
        used: BudgetUsed,
        *,
        last_error: str,
        guidance: str,
    ) -> WorkItemResult:
        work_item.attempts += 1
        used.attempts += 1
        used.executor_runs += 1
        work_item.status = WorkItemStatus.running
        work_item.budget_used = used.model_copy(deep=True)
        await self._persist(work_item)

        attempt_body = self._build_attempt_body(
            work_item,
            previous_error=last_error,
            planner_guidance=guidance,
        )
        attempt_ok, attempt_error = await self._execute_attempt(work_item, used, attempt_body)
        if attempt_ok:
            verification_ok, verification_results, verification_error = (
                await self._run_external_verification(work_item)
            )
            work_item.verification_results = [
                dict(result) for result in verification_results
            ]
            if verification_ok:
                work_item.status = WorkItemStatus.done
                work_item.budget_used = used.model_copy(deep=True)
                await self._persist(work_item)
                return WorkItemResult(
                    work_item_id=work_item.id,
                    status=WorkItemStatus.done,
                    summary=f"Work item {work_item.id} completed with planner guidance.",
                    verification_results=work_item.verification_results,
                    budget_used=used.model_copy(deep=True),
                )
            error = verification_error or "verification failed"
        else:
            error = attempt_error or "execution attempt failed"

        work_item.budget_used = used.model_copy(deep=True)
        await self._persist(work_item)
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.failed,
            summary=f"Work item {work_item.id} guided retry failed.",
            last_error=error,
            verification_results=work_item.verification_results,
            budget_used=used.model_copy(deep=True),
        )

    async def _consult_planner(
        self,
        work_item: WorkItem,
        used: BudgetUsed,
        failure_context: str,
    ) -> tuple[str | None, str | None]:
        if self._consult_manager is None:
            return None, None
        if used.planner_calls >= work_item.budget.max_planner_calls:
            await self._audit_event(
                "consult_planner_budget_exhausted",
                work_item_id=work_item.id,
                planner_calls=used.planner_calls,
                max_planner_calls=work_item.budget.max_planner_calls,
            )
            return None, "planner call budget exhausted"

        used.planner_calls += 1
        work_item.budget_used = used.model_copy(deep=True)
        await self._persist(work_item)

        try:
            guidance = await self._consult_manager.consult(
                work_item_id=work_item.id,
                failure_context=failure_context,
                trace_id=self._trace_id_for_work_item(work_item.id),
            )
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            await self._audit_event(
                "consult_planner_error",
                work_item_id=work_item.id,
                error=str(exc),
            )
            return None, f"planner consult error: {exc}"
        if guidance is None:
            await self._audit_event(
                "consult_planner_timeout",
                work_item_id=work_item.id,
            )
            return None, "planner consult timed out"
        await self._audit_event(
            "consult_planner_guidance_received",
            work_item_id=work_item.id,
        )
        return guidance, None

    async def _trigger_replan(
        self,
        work_item: WorkItem,
        used: BudgetUsed,
        failure_context: str,
    ) -> WorkItemResult | None:
        if self._replan_manager is None:
            return None

        try:
            replan_enqueued = await self._replan_manager.trigger_replan(
                work_item_id=work_item.id,
                original_goal=work_item.body,
                failure_history=[
                    {
                        "phase": "execution",
                        "error": failure_context,
                        "attempts": work_item.attempts,
                    }
                ],
                trace_id=self._trace_id_for_work_item(work_item.id),
                current_depth=0,
            )
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            failure_context = f"{failure_context}; replan trigger error: {exc}"
            replan_enqueued = False
            await self._audit_event(
                "replan_trigger_error",
                work_item_id=work_item.id,
                error=str(exc),
            )

        work_item.budget_used = used.model_copy(deep=True)
        if replan_enqueued:
            work_item.status = WorkItemStatus.stuck
            await self._persist(work_item)
            await self._audit_event(
                "replan_triggered",
                work_item_id=work_item.id,
            )
            return WorkItemResult(
                work_item_id=work_item.id,
                status=WorkItemStatus.stuck,
                summary=f"Work item {work_item.id} stuck; replan requested.",
                last_error=failure_context,
                verification_results=work_item.verification_results,
                budget_used=used.model_copy(deep=True),
            )

        work_item.status = WorkItemStatus.failed
        await self._persist(work_item)
        await self._audit_event(
            "recovery_exhausted",
            work_item_id=work_item.id,
            failure_context=failure_context,
        )
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.failed,
            summary=f"Work item {work_item.id} failed after recovery exhausted.",
            last_error=failure_context,
            verification_results=work_item.verification_results,
            budget_used=used.model_copy(deep=True),
        )

    def _trace_id_for_work_item(self, work_item_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"silas:work-item:{work_item_id}"))

    def _resolve_executor(self, executor_type: WorkItemExecutorType) -> EphemeralExecutor | None:
        if executor_type == WorkItemExecutorType.skill:
            return None
        executor = self._executor_registry.get(executor_type)
        if isinstance(executor, SkillExecutor):
            return None
        if executor is None:
            return None
        if not callable(getattr(executor, "execute", None)):
            return None
        return executor

    def _build_execution_envelope(self, work_item: WorkItem) -> ExecutionEnvelope:
        action = _EXECUTION_ACTIONS.get(work_item.executor_type)
        if action is None:
            raise ValueError(f"unsupported executor_type: {work_item.executor_type.value}")

        timeout_seconds = max(1, work_item.budget.max_wall_time_seconds)
        args = self._resolve_execution_args(work_item)
        return ExecutionEnvelope(
            execution_id=f"{work_item.id}:{work_item.attempts}",
            step_index=max(0, work_item.attempts - 1),
            task_description=work_item.body,
            action=action,
            args=args,
            timeout_seconds=timeout_seconds,
            sandbox_config=SandboxConfig(
                network_access=False,
                max_cpu_seconds=timeout_seconds,
            ),
        )

    def _resolve_execution_args(self, work_item: WorkItem) -> dict[str, object]:
        if work_item.executor_type == WorkItemExecutorType.shell:
            return {"command": self._resolve_shell_command(work_item.body)}

        if work_item.executor_type == WorkItemExecutorType.python:
            parsed = self._parse_json_body(work_item.body)
            if isinstance(parsed, dict) and (
                isinstance(parsed.get("script"), str)
                or isinstance(parsed.get("script_path"), str)
            ):
                return dict(parsed)
            script = work_item.body.strip()
            if not script:
                raise ValueError("python executor requires non-empty work item body")
            return {"script": script}

        raise ValueError(f"unsupported executor_type: {work_item.executor_type.value}")

    def _resolve_shell_command(self, body: str) -> str | list[str]:
        stripped = body.strip()
        if not stripped:
            raise ValueError("shell executor requires non-empty work item body")

        parsed = self._parse_json_body(stripped)
        if isinstance(parsed, dict):
            command = parsed.get("command")
            if isinstance(command, str):
                return command
            if isinstance(command, list):
                return [str(value) for value in command]
        if isinstance(parsed, list):
            return [str(value) for value in parsed]
        return stripped

    def _parse_json_body(self, value: str) -> object | None:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def _merge_execution_budget(
        self,
        used: BudgetUsed,
        result: ExecutionResult,
    ) -> None:
        used.tokens += self._estimate_tokens(result.return_value, result.error)
        used.wall_time_seconds += max(0.0, result.duration_seconds)

    async def _run_external_verification(
        self,
        work_item: WorkItem,
    ) -> tuple[bool, list[dict[str, object]], str | None]:
        checks = list(work_item.verify)
        if not checks:
            return True, [], None

        runner = self._verification_runner
        if runner is None:
            return False, [], "verification runner unavailable"

        try:
            report = await runner.run_checks(checks)
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            return False, [], f"verification runner error: {exc}"

        if not isinstance(report, VerificationReport):
            return False, [], "verification runner returned invalid report"

        results = [
            result.model_dump(mode="json")
            for result in report.results
        ]
        if report.all_passed:
            return True, results, None

        failure_detail = self._format_verification_failures(report)
        return False, results, f"verification failed: {failure_detail}"

    def _format_verification_failures(self, report: VerificationReport) -> str:
        failures = report.failed or [result for result in report.results if not result.passed]
        if not failures:
            return "unknown verification failure"
        parts = [f"{failure.name}: {failure.reason}" for failure in failures]
        return "; ".join(parts)

    async def _check_execution_approval(self, work_item: WorkItem) -> tuple[bool, str]:
        token = work_item.approval_token
        if token is None:
            return False, "missing approval token"

        verifier = self._approval_verifier
        if verifier is None:
            return False, "approval verifier unavailable"

        valid, reason = await verifier.check(token, work_item)
        if not valid:
            return False, reason
        return True, "ok"

    async def _audit_execution_blocked(self, work_item: WorkItem, reason: str) -> None:
        if self._audit is None:
            return
        await self._audit.log(
            "execution_blocked_no_approval",
            work_item_id=work_item.id,
            reason=reason,
        )

    async def _audit_event(self, event: str, **kwargs: object) -> None:
        """Log an audit event if the audit log is available."""
        if self._audit is None:
            return
        await self._audit.log(event, **kwargs)

    async def _mark_blocked(
        self,
        root_item: WorkItem,
        error: str,
        budget_used: BudgetUsed | None = None,
    ) -> WorkItemResult:
        used = budget_used.model_copy(deep=True) if budget_used is not None else root_item.budget_used
        root_item.status = WorkItemStatus.blocked
        root_item.budget_used = used.model_copy(deep=True)
        await self._persist(root_item)
        return WorkItemResult(
            work_item_id=root_item.id,
            status=WorkItemStatus.blocked,
            summary=f"Work item {root_item.id} blocked.",
            last_error=error,
            budget_used=used.model_copy(deep=True),
        )

    async def _persist(self, item: WorkItem) -> None:
        await self._work_item_store.save(item)
        await self._work_item_store.update_status(item.id, item.status, item.budget_used)

    async def _mark_failed(
        self,
        root_item: WorkItem,
        error: str,
        budget_used: BudgetUsed | None = None,
    ) -> WorkItemResult:
        used = budget_used.model_copy(deep=True) if budget_used is not None else root_item.budget_used
        root_item.status = WorkItemStatus.failed
        root_item.budget_used = used.model_copy(deep=True)
        await self._persist(root_item)
        return WorkItemResult(
            work_item_id=root_item.id,
            status=WorkItemStatus.failed,
            summary=f"Work item {root_item.id} failed.",
            last_error=error,
            budget_used=used.model_copy(deep=True),
        )

    async def _resolve_items(
        self,
        root_item: WorkItem,
    ) -> tuple[dict[str, WorkItem], dict[str, set[str]]]:
        items_by_id: dict[str, WorkItem] = {root_item.id: root_item.model_copy(deep=True)}
        prerequisites: dict[str, set[str]] = {}
        pending: list[str] = [root_item.id]

        while pending:
            current_id = pending.pop()
            current = items_by_id[current_id]

            deps = set(current.depends_on)
            if current.id == root_item.id and current.tasks:
                deps.update(current.tasks)

            prerequisites[current_id] = deps

            for dep_id in deps:
                if dep_id in items_by_id:
                    continue
                dependency = await self._work_item_store.get(dep_id)
                if dependency is None:
                    raise ValueError(f"missing dependency '{dep_id}'")
                items_by_id[dep_id] = dependency.model_copy(deep=True)
                pending.append(dep_id)

        return items_by_id, prerequisites

    def _topological_sort(self, prerequisites: dict[str, set[str]]) -> list[str]:
        remaining = {item_id: set(deps) for item_id, deps in prerequisites.items()}
        dependents: dict[str, set[str]] = {item_id: set() for item_id in remaining}

        for item_id, deps in remaining.items():
            for dep_id in deps:
                if dep_id not in remaining:
                    raise ValueError(f"dependency '{dep_id}' is not resolvable")
                dependents[dep_id].add(item_id)

        ready = sorted(item_id for item_id, deps in remaining.items() if not deps)
        order: list[str] = []

        while ready:
            current = ready.pop(0)
            order.append(current)
            for dependent in sorted(dependents[current]):
                if current in remaining[dependent]:
                    remaining[dependent].remove(current)
                if not remaining[dependent] and dependent not in order and dependent not in ready:
                    ready.append(dependent)
            ready.sort()

        if len(order) != len(remaining):
            unresolved = sorted(set(remaining) - set(order))
            chain = " -> ".join(unresolved)
            raise ValueError(f"circular dependency detected: {chain}")

        return order

    def _estimate_tokens(self, *values: object) -> int:
        parts: list[str] = []
        for value in values:
            if value is None:
                continue
            if isinstance(value, str):
                text = value
            else:
                text = json.dumps(value, sort_keys=True, default=str)
            if text:
                parts.append(text)

        if not parts:
            return 0

        combined = " ".join(parts)
        return max(1, int(len(combined) / _CHARS_PER_TOKEN))


__all__ = ["LiveWorkItemExecutor", "work_item_from_execution_payload"]
