"""ToolsetMixin — toolset preparation and prompt building."""

from __future__ import annotations

from silas.models.agents import PlanAction, RouteDecision
from silas.models.work import WorkItem, WorkItemStatus, WorkItemType
from silas.tools.approval_required import ApprovalRequiredToolset
from silas.tools.filtered import FilteredToolset
from silas.tools.prepared import PreparedToolset
from silas.tools.skill_toolset import SkillToolset, ToolDefinition

_PROXY_BASE_TOOLS: tuple[tuple[str, str], ...] = (
    ("context_inspect", "Inspect active turn context for routing."),
    ("memory_search", "Retrieve relevant memories before routing."),
    ("tell_user", "Send interim status updates to the user."),
    ("web_search", "Look up current external information."),
)
_PLANNER_BASE_TOOLS: tuple[tuple[str, str], ...] = (
    ("memory_search", "Retrieve historical context before planning."),
    ("request_research", "Delegate research to executor queue."),
    ("validate_plan", "Validate markdown plan structure."),
    ("web_search", "Look up supporting facts for plan quality."),
)

_IN_PROGRESS_STATUSES: tuple[WorkItemStatus, ...] = (
    WorkItemStatus.pending,
    WorkItemStatus.running,
    WorkItemStatus.healthy,
    WorkItemStatus.stuck,
    WorkItemStatus.paused,
)


class ToolsetMixin:
    """Toolset preparation, prompt building, and skill name resolution."""

    async def _prepare_agent_toolsets(
        self,
        *,
        connection_id: str,
        turn_number: int,
    ) -> tuple[ApprovalRequiredToolset | None, ApprovalRequiredToolset | None]:
        tc = self._turn_context()
        resolver = tc.skill_resolver
        if resolver is None:
            await self._audit(
                "skill_toolsets_prepared",
                step=6.5,
                connection_id=connection_id,
                prepared=False,
                reason="skill_resolver_missing",
            )
            return None, None

        active_work_item = await self._find_active_toolset_work_item()
        work_item_for_tools = active_work_item or self._build_synthetic_toolset_work_item(turn_number)
        active_work_item_id = active_work_item.id if active_work_item is not None else None

        proxy_toolset = self._build_role_toolset(
            resolver=resolver,
            work_item=work_item_for_tools,
            agent_role="proxy",
        )
        planner_toolset = self._build_role_toolset(
            resolver=resolver,
            work_item=work_item_for_tools,
            agent_role="planner",
        )
        await self._audit(
            "skill_toolsets_prepared",
            step=6.5,
            connection_id=connection_id,
            prepared=True,
            work_item_id=active_work_item_id,
            proxy_tools=self._tool_names(proxy_toolset),
            planner_tools=self._tool_names(planner_toolset),
        )
        return proxy_toolset, planner_toolset

    async def _find_active_toolset_work_item(self) -> WorkItem | None:
        store = self.work_item_store
        if store is None:
            return None

        for status in _IN_PROGRESS_STATUSES:
            try:
                items = await store.list_by_status(status)
            except (OSError, RuntimeError, ValueError):
                return None
            if not items:
                continue
            ordered = sorted(items, key=lambda item: (item.created_at, item.id))
            return ordered[0].model_copy(deep=True)

        return None

    def _build_role_toolset(
        self,
        *,
        resolver: object,
        work_item: WorkItem,
        agent_role: str,
    ) -> ApprovalRequiredToolset:
        base_tools = self._base_tools_for_role(agent_role)
        allowed_tools = sorted({
            *[tool.name for tool in base_tools],
            *self._available_skill_names(),
            *work_item.skills,
        })

        try:
            prepared = resolver.prepare_toolset(
                work_item=work_item,
                agent_role=agent_role,
                base_toolset=base_tools,
                allowed_tools=allowed_tools,
            )
            if isinstance(prepared, ApprovalRequiredToolset):
                return prepared
        except (OSError, RuntimeError, TypeError, ValueError):
            pass

        return ApprovalRequiredToolset(
            inner=FilteredToolset(
                inner=PreparedToolset(
                    inner=SkillToolset(base_toolset=base_tools, skill_metadata=[]),
                    agent_role=agent_role,
                ),
                allowed_tools=allowed_tools,
            )
        )

    def _base_tools_for_role(self, agent_role: str) -> list[ToolDefinition]:
        catalog = _PLANNER_BASE_TOOLS if agent_role == "planner" else _PROXY_BASE_TOOLS
        return [
            ToolDefinition(
                name=name,
                description=description,
                input_schema={"type": "object"},
            )
            for name, description in catalog
        ]

    def _build_synthetic_toolset_work_item(self, turn_number: int) -> WorkItem:
        scope_id = self._turn_context().scope_id
        return WorkItem(
            id=f"toolset:{scope_id}:{turn_number}",
            type=WorkItemType.task,
            title="Turn-scoped toolset preparation",
            body="No active work item available for this turn.",
            skills=[],
            needs_approval=False,
        )

    def _tool_names(self, toolset: ApprovalRequiredToolset | None) -> list[str]:
        if toolset is None:
            return []
        return [tool.name for tool in toolset.list_tools()]

    def _render_toolset_manifest(self, toolset: ApprovalRequiredToolset | None) -> str:
        if toolset is None:
            return ""
        tools = toolset.list_tools()
        if not tools:
            return ""

        lines: list[str] = []
        for tool in tools:
            description = " ".join(tool.description.split())
            approval_note = " [approval required]" if tool.requires_approval else ""
            lines.append(f"- {tool.name}{approval_note}: {description}")
        return "\n".join(lines)

    def _build_proxy_prompt(
        self,
        message_text: str,
        rendered_context: str,
        *,
        toolset: ApprovalRequiredToolset | None = None,
    ) -> str:
        sections: list[str] = []
        if rendered_context.strip():
            sections.append(f"[CONTEXT]\n{rendered_context}")
        tool_manifest = self._render_toolset_manifest(toolset)
        if tool_manifest:
            sections.append(f"[AVAILABLE TOOLS]\n{tool_manifest}")
        sections.append(f"[USER MESSAGE]\n{message_text}")
        return "\n\n".join(sections)

    def _build_planner_prompt(
        self,
        message_text: str,
        rendered_context: str,
        *,
        toolset: ApprovalRequiredToolset | None = None,
    ) -> str:
        sections: list[str] = []
        if rendered_context.strip():
            sections.append(f"[CONTEXT]\n{rendered_context}")
        tool_manifest = self._render_toolset_manifest(toolset)
        if tool_manifest:
            sections.append(f"[AVAILABLE TOOLS]\n{tool_manifest}")
        sections.append(f"[USER REQUEST]\n{message_text}")
        return "\n\n".join(sections)

    def _route_response_text(self, routed: RouteDecision) -> str:
        if routed.route == "planner":
            return "I need to plan this request before execution. Planner execution is not available yet."
        return routed.response.message if routed.response is not None else ""

    def _available_skill_names(self) -> list[str]:
        registry = self._turn_context().skill_registry
        if registry is None:
            return []
        return [skill.name for skill in registry.list_all()]

    def _extract_plan_actions(self, routed: RouteDecision) -> list[dict[str, object]]:
        raw_actions = getattr(routed, "plan_actions", None)
        if not isinstance(raw_actions, list):
            return []
        normalized: list[dict[str, object]] = []
        for action in raw_actions:
            if isinstance(action, PlanAction):
                normalized.append(action.model_dump(mode="json"))
            elif isinstance(action, dict):
                normalized.append(action)
        return normalized
