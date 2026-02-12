"""Plan executor — converts plan actions to work items and executes them.

Extracted from Stream to reduce file size. Handles plan parsing,
dependency ordering, and work item execution orchestration.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Protocol

from silas.core.plan_parser import MarkdownPlanParser
from silas.models.work import WorkItem, WorkItemResult, WorkItemStatus, WorkItemType


class WorkExecutorProtocol(Protocol):
    async def execute(self, work_item: WorkItem) -> WorkItemResult: ...


async def execute_plan_actions(
    plan_actions: list[dict[str, object]],
    executor: WorkExecutorProtocol,
    *,
    turn_number: int,
    continuation_of: str | None,
) -> str:
    """Convert plan actions to work items and execute them sequentially.

    Returns a summary string describing the outcome.
    """
    try:
        work_items = plan_actions_to_work_items(
            plan_actions, turn_number=turn_number, continuation_of=continuation_of,
        )
        ordered = order_work_items(work_items)
    except ValueError as exc:
        return f"Planner execution failed: {exc}"

    if not ordered:
        return "Planner produced no executable work items."

    results: list[WorkItemResult] = []
    for work_item in ordered:
        result = await executor.execute(work_item)
        results.append(result)
        if result.status != WorkItemStatus.done:
            break

    done_count = sum(1 for r in results if r.status == WorkItemStatus.done)
    failed = [r for r in results if r.status == WorkItemStatus.failed]
    if failed:
        f = failed[0]
        return (
            f"Plan execution summary: {done_count} done, {len(failed)} failed. "
            f"First failure: {f.work_item_id} ({f.last_error or f.summary})."
        )
    return f"Plan execution summary: {done_count} done, 0 failed."


def plan_actions_to_work_items(
    plan_actions: list[dict[str, object]],
    *,
    turn_number: int,
    continuation_of: str | None,
) -> list[WorkItem]:
    """Parse plan actions into WorkItem instances with continuation linking."""
    parser = MarkdownPlanParser()
    work_items: list[WorkItem] = []
    for index, action in enumerate(plan_actions):
        work_item = plan_action_to_work_item(
            action, parser=parser, index=index, turn_number=turn_number,
        )
        if continuation_of and work_item.follow_up_of is None:
            update_data: dict[str, object] = {"follow_up_of": continuation_of}
            if not work_item.input_artifacts_from:
                update_data["input_artifacts_from"] = ["*"]
            work_item = work_item.model_copy(update=update_data)
        work_items.append(work_item)
    return work_items


def plan_action_to_work_item(
    action: Mapping[str, object],
    *,
    parser: MarkdownPlanParser,
    index: int,
    turn_number: int,
) -> WorkItem:
    """Convert a single plan action dict into a WorkItem."""
    plan_markdown = action.get("plan_markdown")
    if isinstance(plan_markdown, str) and plan_markdown.strip():
        return parser.parse(plan_markdown)

    explicit_work_item = action.get("work_item")
    if isinstance(explicit_work_item, Mapping):
        return WorkItem.model_validate(dict(explicit_work_item))

    payload = dict(action)
    payload.setdefault("id", f"plan:{turn_number}:{index + 1}")
    payload.setdefault("type", WorkItemType.task.value)
    payload.setdefault("title", f"Plan action {index + 1}")

    body = payload.get("body")
    if not isinstance(body, str) or not body.strip():
        body = payload.get("instruction")
    if not isinstance(body, str) or not body.strip():
        body = payload.get("description")
    if not isinstance(body, str) or not body.strip():
        body = f"Execute planner action {index + 1}."
    payload["body"] = body

    return WorkItem.model_validate(payload)


def order_work_items(work_items: list[WorkItem]) -> list[WorkItem]:
    """Topological sort of work items by depends_on. Raises on cycles."""
    if not work_items:
        return []

    by_id: dict[str, WorkItem] = {}
    for item in work_items:
        if item.id in by_id:
            raise ValueError(f"duplicate work item id in plan actions: {item.id}")
        by_id[item.id] = item

    prerequisites: dict[str, set[str]] = {
        item.id: {dep_id for dep_id in item.depends_on if dep_id in by_id}
        for item in work_items
    }
    dependents: dict[str, set[str]] = {item_id: set() for item_id in by_id}
    for item_id, deps in prerequisites.items():
        for dep_id in deps:
            dependents[dep_id].add(item_id)

    ready = sorted(item_id for item_id, deps in prerequisites.items() if not deps)
    ordered_ids: list[str] = []

    while ready:
        current = ready.pop(0)
        ordered_ids.append(current)
        for dependent in sorted(dependents[current]):
            prerequisites[dependent].discard(current)
            if not prerequisites[dependent] and dependent not in ordered_ids and dependent not in ready:
                ready.append(dependent)
        ready.sort()

    if len(ordered_ids) != len(by_id):
        unresolved = sorted(set(by_id) - set(ordered_ids))
        raise ValueError(f"circular planner dependency detected: {' -> '.join(unresolved)}")

    return [by_id[item_id] for item_id in ordered_ids]


def extract_skill_name(action: dict[str, object]) -> str | None:
    """Extract skill name from a plan action dict."""
    candidate = (
        action.get("skill_name")
        or action.get("skill")
        or action.get("tool")
    )
    if isinstance(candidate, str) and candidate.strip():
        return candidate
    return None


def extract_skill_inputs(action: dict[str, object]) -> dict[str, object]:
    """Extract skill input arguments from a plan action dict."""
    for key in ("inputs", "args", "arguments"):
        value = action.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


def build_skill_work_item(
    skill_name: str,
    action: dict[str, object],
    turn_number: int,
    requires_approval: bool,
) -> WorkItem:
    """Build a WorkItem for a skill execution from a plan action."""
    title = action.get("title")
    if not isinstance(title, str) or not title.strip():
        title = f"Execute skill: {skill_name}"

    body = action.get("body")
    if not isinstance(body, str) or not body.strip():
        body = f"Planner requested execution of skill '{skill_name}'."

    needs_approval = _resolve_skill_needs_approval(
        action=action,
        requires_approval=requires_approval,
    )

    return WorkItem(
        id=f"skill:{turn_number}:{uuid.uuid4().hex}",
        type=WorkItemType.task,
        title=title,
        body=body,
        needs_approval=needs_approval,
        skills=[skill_name],
    )


def _resolve_skill_needs_approval(
    *,
    action: Mapping[str, object],
    requires_approval: bool,
) -> bool:
    """Keep skill metadata as the floor for approval requirements.

    Planner output is untrusted input, so it may request less approval than
    the skill definition requires. We only allow planner data to request a
    stricter requirement, never a weaker one.
    """
    planner_value = action.get("needs_approval")
    if planner_value is None:
        nested_work_item = action.get("work_item")
        if isinstance(nested_work_item, Mapping):
            planner_value = nested_work_item.get("needs_approval")

    if isinstance(planner_value, bool):
        return requires_approval or planner_value
    return requires_approval


__all__ = [
    "build_skill_work_item",
    "execute_plan_actions",
    "extract_skill_inputs",
    "extract_skill_name",
    "order_work_items",
    "plan_action_to_work_item",
    "plan_actions_to_work_items",
]
