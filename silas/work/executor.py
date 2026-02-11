from __future__ import annotations

import json
from datetime import datetime, timezone

from silas.models.work import BudgetUsed, WorkItem, WorkItemResult, WorkItemStatus
from silas.protocols.work import WorkItemStore
from silas.skills.executor import SkillExecutor

_CHARS_PER_TOKEN = 3.5


class LiveWorkItemExecutor:
    def __init__(self, skill_executor: SkillExecutor, work_item_store: WorkItemStore) -> None:
        self._skill_executor = skill_executor
        self._work_item_store = work_item_store

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
                if execution_results.get(dep_id, None) is None
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

        self._skill_executor.set_work_item(work_item)
        try:
            for _ in range(max_attempts):
                if used.exceeds(work_item.budget):
                    last_error = "budget exhausted before attempt"
                    break

                attempt_started = datetime.now(timezone.utc)
                work_item.attempts += 1
                used.attempts += 1
                used.executor_runs += 1

                work_item.status = WorkItemStatus.running
                work_item.budget_used = used.model_copy(deep=True)
                await self._persist(work_item)

                attempt_ok = True
                if not work_item.skills:
                    used.tokens += self._estimate_tokens(work_item.body)

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
                        attempt_ok = False
                        last_error = skill_result.error or f"skill '{skill_name}' failed"
                        break

                if not work_item.skills:
                    used.wall_time_seconds += (
                        datetime.now(timezone.utc) - attempt_started
                    ).total_seconds()

                work_item.budget_used = used.model_copy(deep=True)
                if attempt_ok:
                    work_item.status = WorkItemStatus.done
                    await self._persist(work_item)
                    return WorkItemResult(
                        work_item_id=work_item.id,
                        status=WorkItemStatus.done,
                        summary=f"Work item {work_item.id} completed.",
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
                budget_used=used.model_copy(deep=True),
            )
        finally:
            self._skill_executor.set_work_item(None)

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
