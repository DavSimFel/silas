from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime

from silas.execution.python_exec import PythonExecutor
from silas.execution.sandbox import SubprocessSandboxManager
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

_CHARS_PER_TOKEN = 3.5

_EXECUTION_ACTIONS: dict[WorkItemExecutorType, str] = {
    WorkItemExecutorType.shell: "shell_exec",
    WorkItemExecutorType.python: "python_exec",
}


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
    ) -> None:
        self._skill_executor = skill_executor
        self._work_item_store = work_item_store
        self._approval_verifier = approval_verifier
        self._verification_runner = verification_runner
        self._audit = audit

        sandbox_manager = SubprocessSandboxManager()
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

                attempt_ok, attempt_error = await self._execute_attempt(work_item, used)
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
    ) -> tuple[bool, str | None]:
        if work_item.executor_type == WorkItemExecutorType.skill:
            return await self._execute_skill_attempt(work_item, used)
        return await self._execute_registered_attempt(work_item, used)

    async def _execute_skill_attempt(
        self,
        work_item: WorkItem,
        used: BudgetUsed,
    ) -> tuple[bool, str | None]:
        if not work_item.skills:
            attempt_started = datetime.now(UTC)
            used.tokens += self._estimate_tokens(work_item.body)
            used.wall_time_seconds += (datetime.now(UTC) - attempt_started).total_seconds()
            return True, None

        for skill_name in work_item.skills:
            skill_result = await self._skill_executor.execute(
                skill_name,
                {
                    "work_item_id": work_item.id,
                    "title": work_item.title,
                    "body": work_item.body,
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


__all__ = ["LiveWorkItemExecutor"]
