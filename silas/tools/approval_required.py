from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from silas.tools.skill_toolset import (
    ApprovalRequest,
    ToolCallResult,
    ToolDefinition,
    ToolsetProtocol,
)


@dataclass(slots=True)
class PendingApprovalCall:
    request_id: str
    tool_name: str
    arguments: dict[str, object]
    created_at: datetime


class ApprovalRequiredToolset:
    """Pauses execution for tools marked requires_approval until resume()."""

    def __init__(self, inner: ToolsetProtocol) -> None:
        self.inner = inner
        self._pending: dict[str, PendingApprovalCall] = {}

    def list_tools(self) -> list[ToolDefinition]:
        return self.inner.list_tools()

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        tool = self._find_tool(tool_name)
        if tool is None:
            return self.inner.call(tool_name, arguments)

        if not tool.requires_approval:
            return self.inner.call(tool_name, arguments)

        request_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc)
        pending = PendingApprovalCall(
            request_id=request_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            created_at=created_at,
        )
        self._pending[request_id] = pending

        return ToolCallResult(
            status="approval_required",
            approval_request=ApprovalRequest(
                request_id=request_id,
                tool_name=tool_name,
                arguments=dict(arguments),
                created_at=created_at,
            ),
        )

    def resume(self, request_id: str, approved: bool) -> ToolCallResult:
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return ToolCallResult(status="error", error=f"unknown approval request: {request_id}")

        if not approved:
            return ToolCallResult(
                status="denied",
                error=f"approval declined for tool '{pending.tool_name}'",
            )

        return self.inner.call(pending.tool_name, dict(pending.arguments))

    def pending_requests(self) -> list[ApprovalRequest]:
        return [
            ApprovalRequest(
                request_id=pending.request_id,
                tool_name=pending.tool_name,
                arguments=dict(pending.arguments),
                created_at=pending.created_at,
            )
            for pending in self._pending.values()
        ]

    def _find_tool(self, tool_name: str) -> ToolDefinition | None:
        for tool in self.inner.list_tools():
            if tool.name == tool_name:
                return tool
        return None


__all__ = ["ApprovalRequiredToolset", "PendingApprovalCall"]
