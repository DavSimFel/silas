"""PlannerMixin — plan resolution, approval, and skill execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from silas.agents.structured import run_structured_agent
from silas.core.plan_executor import (
    build_skill_work_item,
    execute_plan_actions,
    extract_skill_inputs,
    extract_skill_name,
    plan_action_to_work_item,
    resolve_work_item_approval,
)
from silas.core.plan_parser import MarkdownPlanParser
from silas.models.agents import AgentResponse, InteractionMode, RouteDecision
from silas.models.approval import ApprovalScope, ApprovalToken, ApprovalVerdict
from silas.models.work import WorkItem

if TYPE_CHECKING:
    from silas.core.stream._base import StreamBase
    from silas.tools.approval_required import ApprovalRequiredToolset


class PlannerMixin(StreamBase if TYPE_CHECKING else object):  # type: ignore[misc]
    """Plan resolution, approval gating, and skill action execution."""

    async def _handle_planner_route(
        self,
        routed: RouteDecision,
        response_text: str,
        connection_id: str,
        turn_number: int,
        message_text: str,
        rendered_context: str,
        interaction_mode: InteractionMode,
        planner_toolset: ApprovalRequiredToolset | None,
    ) -> str:
        plan_flow_payload: dict[str, object] = {
            "actions_seen": 0,
            "skills_executed": 0,
            "skills_skipped": 0,
            "approval_requested": 0,
            "approval_approved": 0,
            "approval_declined": 0,
        }
        if routed.route == "planner":
            plan_actions, planner_message = await self._resolve_plan_actions(
                routed=routed,
                message_text=message_text,
                rendered_context=rendered_context,
                turn_number=turn_number,
                planner_toolset=planner_toolset,
            )
            plan_flow_payload["actions_seen"] = len(plan_actions)
            if plan_actions:
                continuation_of = routed.continuation_of
                for action in plan_actions:
                    raw = action.get("continuation_of")
                    if isinstance(raw, str) and raw.strip():
                        continuation_of = raw
                        break
                work_exec_summary = await self._try_work_execution(
                    plan_actions,
                    turn_number,
                    continuation_of,
                    interaction_mode,
                    connection_id,
                )
                if work_exec_summary is not None:
                    response_text = work_exec_summary
                else:
                    response_text, plan_flow_payload = await self._execute_planner_skill_actions(
                        plan_actions=plan_actions,
                        connection_id=connection_id,
                        turn_number=turn_number,
                        fallback_response=response_text,
                        interaction_mode=interaction_mode,
                    )
            else:
                if planner_message:
                    response_text = planner_message
                await self._audit(
                    "planner_stub_used",
                    turn_number=turn_number,
                    reason=routed.reason,
                )
        await self._audit(
            "plan_approval_flow_checked", step=12, turn_number=turn_number, **plan_flow_payload
        )
        return response_text

    async def _resolve_plan_actions(
        self,
        *,
        routed: RouteDecision,
        message_text: str,
        rendered_context: str,
        turn_number: int,
        planner_toolset: ApprovalRequiredToolset | None,
    ) -> tuple[list[dict[str, object]], str | None]:
        planner = self._turn_context().planner
        if planner is None:
            await self._audit("planner_handoff_missing", turn_number=turn_number)
            return [], None

        planner_output = await run_structured_agent(
            agent=planner,
            prompt=self._build_planner_prompt(
                message_text,
                rendered_context,
                toolset=planner_toolset,
            ),
            call_name="planner",
            default_context_profile="planning",
        )
        actions, planner_message = self._extract_plan_actions_from_planner_output(planner_output)
        await self._audit(
            "planner_handoff_invoked",
            turn_number=turn_number,
            output_type=type(planner_output).__name__,
            actions=len(actions),
        )
        if actions:
            return actions, planner_message

        # Legacy fallback for compatibility while planner output contracts converge.
        fallback_actions = self._extract_plan_actions(routed)
        if fallback_actions:
            await self._audit(
                "planner_handoff_fallback_proxy_actions",
                turn_number=turn_number,
                actions=len(fallback_actions),
            )
        return fallback_actions, planner_message

    def _extract_plan_actions_from_planner_output(
        self,
        planner_output: object,
    ) -> tuple[list[dict[str, object]], str | None]:
        if isinstance(planner_output, RouteDecision):
            planner_message = None
            if planner_output.response is not None:
                planner_message = planner_output.response.message
            return self._extract_plan_actions(planner_output), planner_message

        if isinstance(planner_output, AgentResponse):
            return self._extract_plan_actions_from_agent_response(
                planner_output
            ), planner_output.message

        try:
            response = AgentResponse.model_validate(planner_output)
        except ValidationError:
            return [], None
        return self._extract_plan_actions_from_agent_response(response), response.message

    def _extract_plan_actions_from_agent_response(
        self,
        response: AgentResponse,
    ) -> list[dict[str, object]]:
        plan_action = response.plan_action
        if plan_action is None:
            return []

        action_payload: dict[str, object] = {
            "action": plan_action.action.value,
        }
        if plan_action.plan_markdown:
            action_payload["plan_markdown"] = plan_action.plan_markdown
        if plan_action.continuation_of:
            action_payload["continuation_of"] = plan_action.continuation_of
        if plan_action.interaction_mode_override is not None:
            action_payload["interaction_mode_override"] = (
                plan_action.interaction_mode_override.value
            )
        return [action_payload]

    async def _try_work_execution(
        self,
        plan_actions: list[dict[str, object]],
        turn_number: int,
        continuation_of: str | None,
        interaction_mode: InteractionMode,
        connection_id: str,
    ) -> str | None:
        """Try executing plan actions via work executor. Returns summary or None."""
        executor = self._turn_context().work_executor
        if executor is None:
            return None

        plan_actions_with_mode = [
            {**action, "interaction_mode": interaction_mode.value} for action in plan_actions
        ]
        approved_actions = await self._ensure_plan_action_approvals(
            plan_actions_with_mode,
            turn_number=turn_number,
            connection_id=connection_id,
        )
        if not approved_actions:
            return "Plan execution skipped: approval was not granted."

        summary = await execute_plan_actions(
            approved_actions,
            executor,
            turn_number=turn_number,
            continuation_of=continuation_of,
        )
        await self._audit("planner_actions_executed", turn_number=turn_number, summary=summary)
        return summary

    async def _ensure_plan_action_approvals(
        self,
        plan_actions: list[dict[str, object]],
        *,
        turn_number: int,
        connection_id: str,
    ) -> list[dict[str, object]]:
        """Attach approval tokens to plan actions when required and available."""
        if self._approval_flow is None:
            return plan_actions

        approved_actions: list[dict[str, object]] = []
        parser = MarkdownPlanParser()
        for index, action in enumerate(plan_actions):
            if action.get("approval_token") is not None:
                approved_actions.append(action)
                continue

            try:
                work_item = plan_action_to_work_item(
                    action,
                    parser=parser,
                    index=index,
                    turn_number=turn_number,
                )
            except ValueError:
                approved_actions.append(action)
                continue

            skill_name = extract_skill_name(action) or "plan_action"
            prepared_work_item = await resolve_work_item_approval(
                work_item,
                standing_approval_resolver=self._resolve_standing_approval_token,
                manual_approval_requester=lambda unresolved, skill_name=skill_name, connection_id=connection_id: (
                    self._approval_flow.request_skill_approval(
                        work_item=unresolved,
                        scope=ApprovalScope.full_plan,
                        skill_name=skill_name,
                        connection_id=connection_id,
                    )
                ),
            )
            if prepared_work_item is None or prepared_work_item.approval_token is None:
                await self._audit(
                    "planner_action_approval_declined",
                    turn_number=turn_number,
                    action_index=index,
                    skill_name=skill_name,
                    verdict="declined_or_missing",
                )
                continue

            if prepared_work_item.approval_token.scope == ApprovalScope.standing:
                await self._audit(
                    "planner_action_standing_approval_attached",
                    turn_number=turn_number,
                    action_index=index,
                    skill_name=skill_name,
                )
            else:
                await self._audit(
                    "planner_action_approval_attached",
                    turn_number=turn_number,
                    action_index=index,
                    skill_name=skill_name,
                )

            approved_actions.append(
                {
                    **action,
                    "approval_token": prepared_work_item.approval_token.model_dump(mode="python"),
                }
            )

        return approved_actions

    def _resolve_standing_approval_token(self, work_item: WorkItem) -> ApprovalToken | None:
        """Resolve standing approval for spawned items so manual review can be skipped."""
        approval_manager = self._turn_context().approval_manager
        if approval_manager is None:
            return None
        check_standing = getattr(approval_manager, "check_standing_approval", None)
        if not callable(check_standing):
            return None
        return check_standing(work_item, self.goal_manager)

    async def _execute_planner_skill_actions(
        self,
        plan_actions: list[dict[str, object]],
        connection_id: str,
        turn_number: int,
        fallback_response: str,
        interaction_mode: InteractionMode,
    ) -> tuple[str, dict[str, int]]:
        """Execute skill-based plan actions with approval flow."""
        payload: dict[str, int] = {
            "actions_seen": len(plan_actions),
            "skills_executed": 0,
            "skills_skipped": 0,
            "approval_requested": 0,
            "approval_approved": 0,
            "approval_declined": 0,
        }

        tc = self._turn_context()
        skill_registry = tc.skill_registry
        skill_executor = tc.skill_executor
        if skill_registry is None or skill_executor is None:
            return fallback_response, payload

        summary_lines: list[str] = []
        for action in plan_actions:
            line, action_payload = await self._execute_single_skill_action(
                action,
                connection_id,
                turn_number,
                skill_registry,
                skill_executor,
                interaction_mode,
            )
            if line is not None:
                summary_lines.append(line)
            for key, val in action_payload.items():
                payload[key] = payload.get(key, 0) + val

        if summary_lines:
            return "\n".join(summary_lines), payload
        return fallback_response, payload

    async def _execute_single_skill_action(
        self,
        action: dict[str, object],
        connection_id: str,
        turn_number: int,
        skill_registry: object,
        skill_executor: object,
        interaction_mode: InteractionMode,
    ) -> tuple[str | None, dict[str, int]]:
        """Execute a single skill action, returning (summary_line, counters)."""
        counters: dict[str, int] = {}
        skill_name = extract_skill_name(action)
        if not skill_name:
            return None, counters

        skill_def = skill_registry.get(skill_name)
        await self._audit(
            "planner_skill_action_checked",
            turn_number=turn_number,
            skill_name=skill_name,
            skill_registered=skill_def is not None,
        )
        if skill_def is None:
            counters["skills_skipped"] = 1
            return f"Skipped skill '{skill_name}': skill not registered.", counters

        work_item = build_skill_work_item(
            skill_name,
            action,
            turn_number,
            skill_def.requires_approval,
            interaction_mode=interaction_mode,
        )

        if skill_def.requires_approval:
            counters["approval_requested"] = 1
            await self._audit(
                "approval_requested",
                turn_number=turn_number,
                skill_name=skill_name,
                scope=ApprovalScope.tool_type.value,
            )
            decision, token = await self._approval_flow.request_skill_approval(
                work_item=work_item,
                scope=ApprovalScope.tool_type,
                skill_name=skill_name,
                connection_id=connection_id,
            )
            if decision is None or decision.verdict != ApprovalVerdict.approved or token is None:
                counters["approval_declined"] = 1
                counters["skills_skipped"] = 1
                await self._audit(
                    "skill_execution_skipped_approval",
                    turn_number=turn_number,
                    skill_name=skill_name,
                    verdict=decision.verdict.value if decision is not None else "timed_out",
                )
                return f"Skipped skill '{skill_name}': approval declined.", counters

            counters["approval_approved"] = 1
            work_item.approval_token = token

        inputs = extract_skill_inputs(action)
        skill_executor.set_work_item(work_item)
        try:
            result = await skill_executor.run_tool(skill_name, inputs)
        finally:
            skill_executor.set_work_item(None)

        if result.success:
            counters["skills_executed"] = 1
            line = f"Executed skill '{skill_name}'."
        else:
            counters["skills_skipped"] = 1
            line = f"Failed skill '{skill_name}': {result.error or 'execution failed'}."

        await self._audit(
            "planner_skill_action_executed",
            turn_number=turn_number,
            skill_name=skill_name,
            success=result.success,
            error=result.error,
        )
        return line, counters
