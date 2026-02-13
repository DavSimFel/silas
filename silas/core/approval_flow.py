"""Approval flow — extracted from Stream to reduce file size.

Handles skill approval card creation, sending, waiting, and response handling.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from silas.models.approval import ApprovalDecision, ApprovalScope, ApprovalToken, ApprovalVerdict

if TYPE_CHECKING:
    from silas.models.work import WorkItem

_APPROVAL_WAIT_LIMIT = timedelta(minutes=5)


class ApprovalManagerProtocol(Protocol):
    def request_approval(self, work_item: WorkItem, scope: ApprovalScope) -> ApprovalToken: ...
    def check_approval(self, token_id: str) -> ApprovalDecision | None: ...
    def resolve(self, token_id: str, verdict: ApprovalVerdict, resolved_by: str) -> ApprovalDecision: ...


class ChannelWithApproval(Protocol):
    async def send(self, recipient_id: str, text: str, reply_to: str | None = ...) -> None: ...
    async def send_approval_card(self, recipient_id: str, card: dict[str, object]) -> None: ...


class ApprovalFlow:
    """Encapsulates the skill approval request/wait/resolve cycle."""

    def __init__(
        self,
        approval_manager: ApprovalManagerProtocol | None,
        channel: ChannelWithApproval | None,
    ) -> None:
        self._approval_manager = approval_manager
        self._channel = channel

    def register_channel_handler(self, channel: object, on_response: object) -> None:
        """Hook approval response handler into the channel if supported."""
        register_handler = getattr(channel, "register_approval_response_handler", None)
        if callable(register_handler) and self._approval_manager is not None:
            register_handler(on_response)

    async def request_skill_approval(
        self,
        work_item: WorkItem,
        scope: ApprovalScope,
        skill_name: str,
        connection_id: str,
    ) -> tuple[ApprovalDecision | None, ApprovalToken | None]:
        """Request approval for a skill execution, wait for response or timeout."""
        if self._approval_manager is None:
            return None, None

        token = self._approval_manager.request_approval(work_item, scope)
        card = build_approval_card(skill_name, token, work_item, scope)
        sent = await self._send_approval_card(connection_id, card)
        if not sent:
            return None, None

        decision = await self._wait_for_approval(token)
        if decision is not None:
            return decision, token

        # Timeout — auto-decline
        try:
            decision = self._approval_manager.resolve(
                token.token_id,
                ApprovalVerdict.declined,
                "system:approval_timeout",
            )
        except (KeyError, ValueError):
            return None, None
        return decision, token

    async def handle_response(
        self,
        token_id: str,
        verdict: ApprovalVerdict,
        resolved_by: str,
    ) -> bool:
        """Process an incoming approval response. Returns True if resolved."""
        if self._approval_manager is None:
            return False

        try:
            self._approval_manager.resolve(token_id, verdict, resolved_by)
        except (KeyError, ValueError):
            return False
        return True

    async def _send_approval_card(self, recipient_id: str, card: dict[str, object]) -> bool:
        if self._channel is None:
            return False
        send_card = getattr(self._channel, "send_approval_card", None)
        if not callable(send_card):
            await self._channel.send(
                recipient_id,
                "Approval required but this channel cannot render approval cards.",
            )
            return False
        await send_card(recipient_id, card)
        return True

    async def _wait_for_approval(self, token: ApprovalToken) -> ApprovalDecision | None:
        if self._approval_manager is None:
            return None

        deadline = min(
            token.expires_at,
            datetime.now(UTC) + _APPROVAL_WAIT_LIMIT,
        )
        while datetime.now(UTC) < deadline:
            decision = self._approval_manager.check_approval(token.token_id)
            if decision is not None:
                return decision
            await asyncio.sleep(0.1)
        return None


def build_approval_card(
    skill_name: str,
    token: ApprovalToken,
    work_item: WorkItem,
    scope: ApprovalScope,
) -> dict[str, object]:
    """Build the approval card payload for the channel."""
    return {
        "id": token.token_id,
        "title": f"Approve skill: {skill_name}",
        "risk": risk_level(scope),
        "rationale": "This skill requires explicit approval before execution.",
        "details": (
            f"Work item: {work_item.title}\n"
            f"Scope: {scope.value}\n"
            f"Expires: {token.expires_at.isoformat()}"
        ),
        "cta": {
            "approve": "Approve",
            "decline": "Decline",
        },
    }


def risk_level(scope: ApprovalScope) -> str:
    """Map approval scope to risk level string."""
    by_scope = {
        ApprovalScope.full_plan: "high",
        ApprovalScope.single_step: "low",
        ApprovalScope.step_range: "medium",
        ApprovalScope.tool_type: "medium",
        ApprovalScope.skill_install: "high",
        ApprovalScope.credential_use: "high",
        ApprovalScope.budget: "medium",
        ApprovalScope.self_update: "high",
        ApprovalScope.connection_act: "high",
        ApprovalScope.connection_manage: "high",
        ApprovalScope.autonomy_threshold: "high",
        ApprovalScope.standing: "high",
    }
    return by_scope.get(scope, "medium")


__all__ = ["ApprovalFlow", "build_approval_card", "risk_level"]
