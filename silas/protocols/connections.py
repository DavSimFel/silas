from __future__ import annotations

from typing import Protocol, runtime_checkable

from silas.models.approval import ApprovalToken
from silas.models.connections import (
    Connection,
    HealthCheckResult,
    SetupStep,
    SetupStepResponse,
)
from silas.protocols.channels import RichCardChannel


@runtime_checkable
class ConnectionManager(Protocol):
    async def discover_connection(
        self,
        skill_name: str,
        identity_hint: dict[str, object],
    ) -> dict[str, object]: ...

    async def run_setup_flow(
        self,
        skill_name: str,
        identity_hint: dict[str, object],
        responses: list[SetupStepResponse] | None = None,
    ) -> list[SetupStep]: ...

    async def activate_connection(
        self,
        skill_name: str,
        provider: str,
        auth_payload: dict[str, object],
        approval: ApprovalToken | None = None,
    ) -> str: ...

    async def escalate_permission(
        self,
        connection_id: str,
        requested_permissions: list[str],
        reason: str,
        channel: RichCardChannel | None = None,
        recipient_id: str | None = None,
    ) -> bool: ...

    async def run_health_checks(self) -> list[HealthCheckResult]: ...

    async def schedule_proactive_refresh(
        self,
        connection_id: str,
        health: HealthCheckResult | None = None,
    ) -> None: ...

    async def refresh_token(self, connection_id: str) -> bool: ...

    async def recover(self, connection_id: str) -> tuple[bool, str]: ...

    async def list_connections(self, domain: str | None = None) -> list[Connection]: ...


__all__ = ["ConnectionManager"]
