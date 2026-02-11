from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from silas.models.approval import ApprovalDecision
from silas.models.messages import ChannelMessage
from silas.models.work import WorkItem


@runtime_checkable
class ChannelAdapterCore(Protocol):
    @property
    def channel_name(self) -> str: ...

    async def listen(self) -> AsyncIterator[tuple[ChannelMessage, str]]: ...

    async def send(self, recipient_id: str, text: str, reply_to: str | None = None) -> None: ...

    async def send_stream_start(self, connection_id: str) -> None: ...

    async def send_stream_chunk(self, connection_id: str, text: str) -> None: ...

    async def send_stream_end(self, connection_id: str) -> None: ...


@runtime_checkable
class RichCardChannel(ChannelAdapterCore, Protocol):
    supports_secure_input: bool

    async def send_approval_request(self, recipient_id: str, work_item: WorkItem) -> ApprovalDecision: ...

    async def send_gate_approval(
        self,
        recipient_id: str,
        gate_name: str,
        value: str | float,
        context: str,
    ) -> str: ...

    async def send_checkpoint(self, message: str, options: list[dict[str, object]]) -> dict[str, object]: ...

    async def send_batch_review(self, recipient_id: str, batch: object) -> object: ...

    async def send_draft_review(
        self,
        recipient_id: str,
        context: str,
        draft: str,
        metadata: dict[str, object],
    ) -> object: ...

    async def send_decision(
        self,
        recipient_id: str,
        question: str,
        options: list[object],
        allow_freetext: bool,
    ) -> object: ...

    async def send_suggestion(self, recipient_id: str, suggestion: object) -> object: ...

    async def send_autonomy_threshold_review(self, recipient_id: str, proposal: object) -> object: ...

    async def send_secure_input(self, recipient_id: str, request: object) -> object: ...

    async def send_connection_setup_step(self, recipient_id: str, step: object) -> object: ...

    async def send_permission_escalation(
        self,
        recipient_id: str,
        connection_name: str,
        current: list[str],
        requested: list[str],
        reason: str,
    ) -> object: ...

    async def send_connection_failure(self, recipient_id: str, failure: object) -> object: ...


__all__ = ["ChannelAdapterCore", "RichCardChannel"]
