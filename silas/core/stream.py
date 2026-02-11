from __future__ import annotations

import uuid
from dataclasses import dataclass

from silas.agents.structured import run_structured_agent
from silas.core.turn_context import TurnContext
from silas.models.agents import RouteDecision
from silas.models.context import ContextItem, ContextZone
from silas.models.messages import ChannelMessage, SignedMessage, TaintLevel
from silas.protocols.channels import ChannelAdapterCore


@dataclass(slots=True)
class Stream:
    channel: ChannelAdapterCore
    turn_context: TurnContext
    owner_id: str = "owner"
    default_context_profile: str = "conversation"

    async def start(self) -> None:
        async for message, connection_id in self.channel.listen():
            await self._process_turn(message, connection_id)

    async def _process_turn(self, message: ChannelMessage, connection_id: str = "owner") -> str:
        await self._audit("phase1a_noop", step=0, note="active gates precompile skipped")
        await self._audit("phase1a_noop", step=0.5, note="review/proactive queue skipped")
        await self._audit("phase1a_noop", step=1, note="input gates skipped")

        # Step 2: sign/taint classification (minimal deterministic lane for Phase 1a).
        taint = TaintLevel.owner if message.sender_id == self.owner_id else TaintLevel.external
        signed = SignedMessage(
            message=message,
            signature=b"",
            nonce=uuid.uuid4().hex,
            taint=taint,
        )

        # Step 3: add inbound message to chronicle zone if context manager exists.
        self.turn_context.turn_number += 1
        turn_number = self.turn_context.turn_number
        if self.turn_context.context_manager is not None:
            self.turn_context.context_manager.add(
                self.turn_context.scope_id,
                ContextItem(
                    ctx_id=f"chronicle:{turn_number}:{uuid.uuid4().hex}",
                    zone=ContextZone.chronicle,
                    content=f"[{signed.taint.value}] {message.sender_id}: {message.text}",
                    token_count=len(message.text),
                    turn_number=turn_number,
                    source=f"channel:{message.channel}",
                    taint=signed.taint,
                    kind="message",
                ),
            )

        # Step 4: minimal memory retrieval stub.
        if self.turn_context.memory_store is not None and self.turn_context.context_manager is not None:
            recalled = await self.turn_context.memory_store.search_keyword(message.text, limit=3)
            for item in recalled:
                self.turn_context.context_manager.add(
                    self.turn_context.scope_id,
                    ContextItem(
                        ctx_id=f"memory:{item.memory_id}",
                        zone=ContextZone.memory,
                        content=item.content,
                        token_count=len(item.content),
                        turn_number=turn_number,
                        source="memory:auto_retrieve",
                        taint=item.taint,
                        kind="memory",
                    ),
                )

        await self._audit("phase1a_noop", step=5, note="budget enforcement skipped")
        await self._audit("phase1a_noop", step=6, note="toolset pipeline skipped")
        await self._audit("phase1a_noop", step=6.5, note="skill-aware toolset skipped")

        # Step 7: route through Proxy via structured wrapper.
        if self.turn_context.proxy is None:
            raise RuntimeError("turn_context.proxy is required")

        routed = await run_structured_agent(
            agent=self.turn_context.proxy,
            prompt=message.text,
            call_name="proxy",
            default_context_profile=self.default_context_profile,
        )
        if not isinstance(routed, RouteDecision):
            raise TypeError("proxy must return RouteDecision")

        if self.turn_context.context_manager is not None:
            self.turn_context.context_manager.set_profile(self.turn_context.scope_id, routed.context_profile)

        await self._audit("phase1a_noop", step=8, note="output gates skipped")
        await self._audit("phase1a_noop", step=9, note="memory query processing skipped")
        await self._audit("phase1a_noop", step=10, note="memory op processing skipped")
        await self._audit("phase1a_noop", step=11, note="response chronicle persist skipped")
        await self._audit("phase1a_noop", step=11.5, note="raw output ingest skipped")
        await self._audit("phase1a_noop", step=12, note="plan/approval flow skipped")

        # Step 13: send response.
        response_text = routed.response.message if routed.response is not None else ""
        await self.channel.send(connection_id, response_text, reply_to=message.reply_to)

        await self._audit("phase1a_noop", step=14, note="access state updates skipped")
        await self._audit("phase1a_noop", step=15, note="personality/autonomy post-turn updates skipped")
        await self._audit("turn_processed", turn_number=turn_number, route=routed.route)

        return response_text

    async def _audit(self, event: str, **data: object) -> None:
        if self.turn_context.audit is None:
            return
        await self.turn_context.audit.log(event, **data)


__all__ = ["Stream"]
