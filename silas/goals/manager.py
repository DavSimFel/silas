from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

from silas.models.approval import ApprovalDecision, ApprovalScope, ApprovalToken, ApprovalVerdict
from silas.models.goals import Goal, GoalRun, StandingApproval
from silas.models.work import WorkItem, WorkItemType
from silas.protocols.approval import ApprovalVerifier

logger = logging.getLogger(__name__)


class SilasGoalManager:
    def __init__(
        self,
        goals_config: list[Goal],
        work_item_store: object,
        approval_engine: ApprovalVerifier | None = None,
    ) -> None:
        self._work_item_store = work_item_store
        self._approval_engine = approval_engine

        self._goals: dict[str, Goal] = {}
        self._runs_by_goal: dict[str, list[GoalRun]] = {}
        self._standing_approvals_by_id: dict[str, StandingApproval] = {}
        self._approval_ids_by_goal_and_policy: dict[tuple[str, str], list[str]] = {}
        self._background_save_tasks: set[asyncio.Task[Any]] = set()

        for goal in goals_config:
            self.schedule_goal(goal)

    def load_goals(self) -> list[Goal]:
        return [goal.model_copy(deep=True) for goal in self._goals.values()]

    def schedule_goal(self, goal: Goal) -> None:
        stored_goal = goal.model_copy(deep=True)
        if not stored_goal.spawn_policy_hash:
            stored_goal.spawn_policy_hash = self._compute_policy_hash(stored_goal.work_template)

        stored_goal.updated_at = datetime.now(UTC)
        self._goals[stored_goal.goal_id] = stored_goal

    def unschedule_goal(self, goal_id: str) -> None:
        self._goals.pop(goal_id, None)

    def run_goal(self, goal_id: str) -> GoalRun:
        goal = self._goals.get(goal_id)
        if goal is None:
            raise KeyError(f"unknown goal_id: {goal_id}")

        run = GoalRun(run_id=uuid.uuid4().hex, goal_id=goal_id)
        self._runs_by_goal.setdefault(goal_id, []).append(run)

        if not goal.enabled:
            run.transition_to("skipped")
            run.result = {"reason": "goal_disabled"}
            return run.model_copy(deep=True)

        try:
            run.transition_to("running")
            work_item = self._spawn_work_item(goal)
            run.work_item_id = work_item.id

            self._save_work_item(work_item)
            run.result = {
                "work_item_id": work_item.id,
                "goal_id": goal.goal_id,
                "status": "spawned",
            }
            run.transition_to("completed")
            return run.model_copy(deep=True)
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
            run.error = str(exc)
            run.transition_to("failed")
            return run.model_copy(deep=True)

    def get_standing_approval(self, goal_id: str, policy_hash: str) -> StandingApproval | None:
        canonical_hash = self._canonical_policy_hash(policy_hash)
        key = (goal_id, canonical_hash)
        approval_ids = self._approval_ids_by_goal_and_policy.get(key, [])

        for approval_id in reversed(approval_ids):
            approval = self._standing_approvals_by_id.get(approval_id)
            if approval is None:
                continue
            if self._is_approval_active(approval):
                return approval.model_copy(deep=True)

        return None

    def grant_standing_approval(
        self,
        goal_id: str,
        policy_hash: str,
        granted_by: str,
        expires_at: datetime | None,
        max_uses: int | None,
    ) -> StandingApproval:
        canonical_hash = self._canonical_policy_hash(policy_hash)
        approval = StandingApproval(
            approval_id=uuid.uuid4().hex,
            goal_id=goal_id,
            policy_hash=canonical_hash,
            granted_by=granted_by,
            granted_at=datetime.now(UTC),
            expires_at=expires_at,
            max_uses=max_uses,
            uses_remaining=max_uses,
        )
        approval_token = self._issue_standing_token(
            goal_id=goal_id,
            policy_hash=canonical_hash,
            max_uses=max_uses,
        )
        if approval_token is not None:
            approval = approval.model_copy(update={"approval_token": approval_token})

        self._standing_approvals_by_id[approval.approval_id] = approval
        self._approval_ids_by_goal_and_policy.setdefault((goal_id, canonical_hash), []).append(
            approval.approval_id
        )
        return approval.model_copy(deep=True)

    def revoke_standing_approval(self, approval_id: str) -> bool:
        approval = self._standing_approvals_by_id.pop(approval_id, None)
        if approval is None:
            return False

        key = (approval.goal_id, approval.policy_hash)
        ids = self._approval_ids_by_goal_and_policy.get(key)
        if ids is not None:
            self._approval_ids_by_goal_and_policy[key] = [item for item in ids if item != approval_id]
            if not self._approval_ids_by_goal_and_policy[key]:
                self._approval_ids_by_goal_and_policy.pop(key, None)
        return True

    def list_runs(self, goal_id: str, limit: int = 50) -> list[GoalRun]:
        if limit <= 0:
            return []
        runs = self._runs_by_goal.get(goal_id, [])
        return [run.model_copy(deep=True) for run in list(reversed(runs))[:limit]]

    def _spawn_work_item(self, goal: Goal) -> WorkItem:
        payload = json.loads(json.dumps(self._json_safe(goal.work_template)))
        if not isinstance(payload, dict):
            raise ValueError("goal work_template must serialize to a JSON object")

        payload.setdefault("id", uuid.uuid4().hex)
        payload.setdefault("type", WorkItemType.task.value)
        payload.setdefault("title", goal.name)
        payload.setdefault("body", goal.description)
        payload.setdefault("parent", goal.goal_id)
        payload.setdefault("spawned_by", goal.goal_id)
        payload.setdefault("needs_approval", True)

        combined_skills = list(dict.fromkeys([*goal.skills, *list(payload.get("skills", []))]))
        payload["skills"] = combined_skills

        work_item = WorkItem.model_validate(payload)

        approved_with_standing_token = False
        if goal.spawn_policy_hash:
            approval = self._resolve_active_approval(goal.goal_id, goal.spawn_policy_hash)
            if approval is not None:
                approved_with_standing_token, reason = self._verify_standing_approval(
                    goal=goal,
                    spawned_task=work_item,
                    approval=approval,
                )
                if approved_with_standing_token:
                    work_item = work_item.model_copy(
                        update={
                            "needs_approval": False,
                            "approval_token": approval.approval_token.model_copy(deep=True),
                        }
                    )
                    self._consume_approval_use(approval.approval_id)
                else:
                    logger.warning(
                        "Standing approval verification failed for goal %s: %s",
                        goal.goal_id,
                        reason,
                    )
        if goal.standing_approval and not approved_with_standing_token:
            logger.warning(
                "Goal %s has standing_approval enabled but no verified standing token; "
                "spawned task will require interactive approval",
                goal.goal_id,
            )

        return work_item

    def _save_work_item(self, work_item: WorkItem) -> None:
        save_fn = getattr(self._work_item_store, "save", None)
        if save_fn is None:
            return

        result = save_fn(work_item)
        if inspect.isawaitable(result):
            self._run_awaitable(result)

    def _run_awaitable(self, awaitable: Awaitable[Any]) -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        task = loop.create_task(awaitable)
        self._background_save_tasks.add(task)

        def _on_done(t: asyncio.Task[Any]) -> None:
            self._background_save_tasks.discard(t)
            if t.cancelled():
                logger.warning("Background save task was cancelled")
            elif exc := t.exception():
                # Surface persistence failures instead of silently swallowing.
                logger.error("Background save failed: %s", exc, exc_info=exc)

        task.add_done_callback(_on_done)
        return None

    def _resolve_active_approval(self, goal_id: str, policy_hash: str) -> StandingApproval | None:
        canonical_hash = self._canonical_policy_hash(policy_hash)
        key = (goal_id, canonical_hash)
        approval_ids = self._approval_ids_by_goal_and_policy.get(key, [])

        for approval_id in reversed(approval_ids):
            approval = self._standing_approvals_by_id.get(approval_id)
            if approval is None:
                continue
            if self._is_approval_active(approval):
                return approval
        return None

    def _consume_approval_use(self, approval_id: str) -> None:
        approval = self._standing_approvals_by_id.get(approval_id)
        if approval is None or approval.uses_remaining is None:
            return

        next_uses = max(approval.uses_remaining - 1, 0)
        self._standing_approvals_by_id[approval_id] = approval.model_copy(
            update={"uses_remaining": next_uses}
        )

    def _is_approval_active(self, approval: StandingApproval) -> bool:
        now = datetime.now(UTC)
        if approval.expires_at is not None and approval.expires_at <= now:
            return False
        if approval.approval_token is not None:
            token = approval.approval_token
            if token.expires_at <= now:
                return False
            if token.executions_used >= token.max_executions:
                return False
        return not (approval.uses_remaining is not None and approval.uses_remaining <= 0)

    def _issue_standing_token(
        self,
        *,
        goal_id: str,
        policy_hash: str,
        max_uses: int | None,
    ) -> ApprovalToken | None:
        approval_engine = self._approval_engine
        if approval_engine is None:
            return None

        goal = self._goals.get(goal_id)
        if goal is None:
            return None

        goal_work_item = self._goal_as_work_item(goal)
        conditions: dict[str, object] = {"spawn_policy_hash": policy_hash}
        if max_uses is not None:
            conditions["max_executions"] = max_uses

        decision = ApprovalDecision(
            verdict=ApprovalVerdict.approved,
            conditions=conditions,
        )
        try:
            token = self._run_coroutine(
                approval_engine.issue_token(
                    goal_work_item,
                    decision,
                    scope=ApprovalScope.standing,
                )
            )
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            logger.warning(
                "Failed to issue standing approval token for goal %s: %s",
                goal_id,
                exc,
            )
            return None
        if not isinstance(token, ApprovalToken):
            logger.warning("Standing approval issuer returned non-token payload for goal %s", goal_id)
            return None
        return token

    def _verify_standing_approval(
        self,
        *,
        goal: Goal,
        spawned_task: WorkItem,
        approval: StandingApproval,
    ) -> tuple[bool, str]:
        approval_engine = self._approval_engine
        token = approval.approval_token
        if approval_engine is None:
            return False, "approval_engine_unavailable"
        if token is None:
            return False, "standing_token_missing"

        goal_work_item = self._goal_as_work_item(goal)
        try:
            result = self._run_coroutine(
                approval_engine.verify(
                    token=token,
                    work_item=goal_work_item,
                    spawned_task=spawned_task,
                )
            )
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            return False, str(exc)

        if not isinstance(result, tuple) or len(result) != 2:
            return False, "invalid_verify_result"

        valid, reason = result
        if not isinstance(valid, bool):
            return False, "invalid_verify_result"
        if not isinstance(reason, str):
            return False, "invalid_verify_result"
        return valid, reason

    def _goal_as_work_item(self, goal: Goal) -> WorkItem:
        payload = json.loads(json.dumps(self._json_safe(goal.work_template)))
        if not isinstance(payload, dict):
            raise ValueError("goal work_template must serialize to a JSON object")
        payload["id"] = goal.goal_id
        payload["type"] = WorkItemType.goal.value
        payload.setdefault("title", goal.name)
        payload.setdefault("body", goal.description)
        payload.setdefault("schedule", "always_on")
        payload["skills"] = list(dict.fromkeys([*goal.skills, *list(payload.get("skills", []))]))
        return WorkItem.model_validate(payload)

    def _run_coroutine(self, awaitable: Awaitable[Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        raise RuntimeError(
            "Cannot run goal approval operations from an active event loop; "
            "run_goal() must be called from synchronous scheduler context"
        )

    def _canonical_policy_hash(self, policy_hash: str) -> str:
        normalized = policy_hash.strip().lower()
        if len(normalized) == 64 and all(c in "0123456789abcdef" for c in normalized):
            return normalized
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _compute_policy_hash(self, work_template: dict[str, object]) -> str:
        canonical_json = json.dumps(
            self._json_safe(work_template),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def _json_safe(self, value: object) -> object:
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat()
        if isinstance(value, dict):
            return {str(k): self._json_safe(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self._json_safe(item) for item in value]
        if isinstance(value, set):
            return sorted(self._json_safe(item) for item in value)
        if isinstance(value, str | int | float | bool) or value is None:
            return value
        if hasattr(value, "model_dump"):
            model_dump = value.model_dump
            if callable(model_dump):
                return self._json_safe(model_dump(mode="json"))
        return str(value)


__all__ = ["SilasGoalManager"]
