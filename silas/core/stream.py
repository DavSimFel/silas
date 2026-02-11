"""The Stream — Silas's permanent orchestration session."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from silas.agents.structured import run_structured_agent
from silas.core.token_counter import HeuristicTokenCounter
from silas.core.turn_context import TurnContext
from silas.models.agents import RouteDecision
from silas.models.context import ContextItem, ContextZone
from silas.models.messages import ChannelMessage, SignedMessage, TaintLevel
from silas.protocols.channels import ChannelAdapterCore

_counter = HeuristicTokenCounter()


@dataclass(slots=True)
class Stream:
    channel: ChannelAdapterCore
    turn_context: TurnContext
    owner_id: str = "owner"
    default_context_profile: str = "conversation"

    async def start(self) -> None:
        await self._rehydrate()
        async for message, connection_id in self.channel.listen():
            await self._process_turn(message, connection_id)

    async def _rehydrate(self) -> None:
        """Restore state from previous run (spec §5.1.3)."""
        tc = self.turn_context

        # Step 1-2: Load recent chronicle entries
        if tc.chronicle_store is not None and tc.context_manager is not None:
            recent = await tc.chronicle_store.get_recent(tc.scope_id, limit=50)
            for item in recent:
                tc.context_manager.add(tc.scope_id, item)
            if recent:
                # Restore turn number from last entry
                tc.turn_number = max(item.turn_number for item in recent)

        # Step 3: Search memory for user profile
        if tc.memory_store is not None and tc.context_manager is not None:
            profile_items = await tc.memory_store.search_keyword("user profile preferences", limit=1)
            for item in profile_items:
                tc.context_manager.add(
                    tc.scope_id,
                    ContextItem(
                        ctx_id=f"memory:profile:{item.memory_id}",
                        zone=ContextZone.memory,
                        content=item.content,
                        token_count=_counter.count(item.content),
                        turn_number=tc.turn_number,
                        source="memory:profile",
                        taint=item.taint,
                        kind="memory",
                        pinned=True,
                    ),
                )

        # Step 5: System message
        await self._audit("stream_rehydrated", turn_number=tc.turn_number)

    async def _process_turn(self, message: ChannelMessage, connection_id: str = "owner") -> str:
        await self._audit("phase1a_noop", step=0, note="active gates precompile skipped")
        await self._audit("phase1a_noop", step=0.5, note="review/proactive queue skipped")
        await self._audit("phase1a_noop", step=1, note="input gates skipped")

        # Step 2: sign/taint classification
        taint = TaintLevel.owner if message.sender_id == self.owner_id else TaintLevel.external
        signed = SignedMessage(
            message=message,
            signature=b"",
            nonce=uuid.uuid4().hex,
            taint=taint,
        )

        # Step 3: add inbound message to chronicle zone + persist
        self.turn_context.turn_number += 1
        turn_number = self.turn_context.turn_number
        chronicle_item = ContextItem(
            ctx_id=f"chronicle:{turn_number}:{uuid.uuid4().hex}",
            zone=ContextZone.chronicle,
            content=f"[{signed.taint.value}] {message.sender_id}: {message.text}",
            token_count=_counter.count(message.text),
            turn_number=turn_number,
            source=f"channel:{message.channel}",
            taint=signed.taint,
            kind="message",
        )
        if self.turn_context.context_manager is not None:
            self.turn_context.context_manager.add(self.turn_context.scope_id, chronicle_item)
        if self.turn_context.chronicle_store is not None:
            await self.turn_context.chronicle_store.append(self.turn_context.scope_id, chronicle_item)

        # Step 4: auto-retrieve memories
        if self.turn_context.memory_store is not None and self.turn_context.context_manager is not None:
            recalled = await self.turn_context.memory_store.search_keyword(message.text, limit=3)
            for item in recalled:
                self.turn_context.context_manager.add(
                    self.turn_context.scope_id,
                    ContextItem(
                        ctx_id=f"memory:{item.memory_id}",
                        zone=ContextZone.memory,
                        content=item.content,
                        token_count=_counter.count(item.content),
                        turn_number=turn_number,
                        source="memory:auto_retrieve",
                        taint=item.taint,
                        kind="memory",
                    ),
                )

        await self._audit("phase1a_noop", step=5, note="budget enforcement skipped")
        await self._audit("phase1a_noop", step=6, note="toolset pipeline skipped")
        await self._audit("phase1a_noop", step=6.5, note="skill-aware toolset skipped")

        # Step 7: route through Proxy
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

        # Step 11: persist response to chronicle
        response_text = routed.response.message if routed.response is not None else ""
        response_item = ContextItem(
            ctx_id=f"chronicle:{turn_number}:resp:{uuid.uuid4().hex}",
            zone=ContextZone.chronicle,
            content=f"Silas: {response_text}",
            token_count=_counter.count(response_text),
            turn_number=turn_number,
            source="agent:proxy",
            taint=TaintLevel.owner,
            kind="message",
        )
        if self.turn_context.context_manager is not None:
            self.turn_context.context_manager.add(self.turn_context.scope_id, response_item)
        if self.turn_context.chronicle_store is not None:
            await self.turn_context.chronicle_store.append(self.turn_context.scope_id, response_item)

        await self._audit("phase1a_noop", step=11.5, note="raw output ingest skipped")
        await self._audit("phase1a_noop", step=12, note="plan/approval flow skipped")

        # Step 13: send response
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
