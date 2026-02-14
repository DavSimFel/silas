"""RehydrationMixin — startup state restoration from stores."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from silas.core.token_counter import HeuristicTokenCounter
from silas.models.context import ContextItem, ContextSubscription, ContextZone
from silas.models.messages import TaintLevel
from silas.models.work import WorkItem, WorkItemStatus

if TYPE_CHECKING:
    from silas.core.context_manager import LiveContextManager
    from silas.core.stream._base import StreamBase
    from silas.core.turn_context import TurnContext
    from silas.protocols.work import WorkItemStore

_counter = HeuristicTokenCounter()
_IN_PROGRESS_STATUSES: tuple[WorkItemStatus, ...] = (
    WorkItemStatus.pending,
    WorkItemStatus.running,
    WorkItemStatus.healthy,
    WorkItemStatus.stuck,
    WorkItemStatus.paused,
)


class RehydrationMixin(StreamBase if TYPE_CHECKING else object):  # type: ignore[misc]
    """Startup state restoration: context, chronicle, memory, proposals."""

    async def _rehydrate(self) -> None:
        """Restore state from previous run (spec §5.1.3)."""
        tc = self.turn_context
        cm = self._get_context_manager()
        session_id = self._ensure_session_id()
        known_scopes = self._known_scopes()

        await self._rehydrate_system_zone(cm, known_scopes)
        await self._rehydrate_chronicle(tc, cm, known_scopes)
        await self._rehydrate_all_scope_memories(tc, cm, session_id, known_scopes)

        in_progress_items = await self._list_in_progress_work_items()
        await self._restore_context_subscriptions(cm, in_progress_items)
        await self._add_rehydration_system_message(cm, known_scopes)
        await self._resume_in_progress_work_items(in_progress_items)

        # Persona state is intentionally loaded on demand to avoid eager scope fan-out.
        self._pending_persona_scopes = set(known_scopes)
        await self._rehydrate_pending_proposals(known_scopes)

        await self._audit(
            "stream_rehydrated",
            turn_number=tc.turn_number,
            scopes=known_scopes,
        )

    async def _rehydrate_system_zone(
        self,
        cm: LiveContextManager | None,
        scopes: list[str],
    ) -> None:
        if cm is None:
            await self._audit("stream_startup_dependency_missing", dependency="context_manager")
            return

        constitution_content = self._constitution_content()
        tools_content = self._tool_descriptions_content()
        config_content = self._configuration_content()

        for scope_id in scopes:
            self._replace_context_item(
                cm,
                scope_id,
                ContextItem(
                    ctx_id="system:constitution",
                    zone=ContextZone.system,
                    content=constitution_content,
                    token_count=_counter.count(constitution_content),
                    created_at=datetime.now(UTC),
                    turn_number=self._turn_context().turn_number,
                    source="system:constitution",
                    taint=TaintLevel.owner,
                    kind="system",
                    pinned=True,
                ),
            )
            self._replace_context_item(
                cm,
                scope_id,
                ContextItem(
                    ctx_id="system:tools",
                    zone=ContextZone.system,
                    content=tools_content,
                    token_count=_counter.count(tools_content),
                    created_at=datetime.now(UTC),
                    turn_number=self._turn_context().turn_number,
                    source="system:tools",
                    taint=TaintLevel.owner,
                    kind="system",
                    pinned=True,
                ),
            )
            self._replace_context_item(
                cm,
                scope_id,
                ContextItem(
                    ctx_id="system:configuration",
                    zone=ContextZone.system,
                    content=config_content,
                    token_count=_counter.count(config_content),
                    created_at=datetime.now(UTC),
                    turn_number=self._turn_context().turn_number,
                    source="system:configuration",
                    taint=TaintLevel.owner,
                    kind="system",
                    pinned=True,
                ),
            )

    async def _rehydrate_chronicle(
        self,
        tc: TurnContext,
        cm: LiveContextManager | None,
        scopes: list[str],
    ) -> None:
        if cm is None:
            await self._audit("stream_startup_dependency_missing", dependency="context_manager")
            return
        if tc.chronicle_store is None:
            await self._audit("stream_startup_dependency_missing", dependency="chronicle_store")
            return

        max_entries = self._rehydration_max_chronicle_entries()
        mask_after_turns = self._observation_mask_after_turns()
        max_seen_turn = tc.turn_number

        for scope_id in scopes:
            recent = await tc.chronicle_store.get_recent(scope_id, limit=max_entries)
            scope_latest_turn = max_seen_turn
            if recent:
                scope_latest_turn = max(item.turn_number for item in recent)
                max_seen_turn = max(max_seen_turn, scope_latest_turn)
            for item in recent:
                hydrated = self._masked_if_stale(item, scope_latest_turn, mask_after_turns)
                cm.add(scope_id, hydrated.model_copy(update={"kind": "message"}))

        tc.turn_number = max_seen_turn

    async def _rehydrate_all_scope_memories(
        self,
        tc: TurnContext,
        cm: LiveContextManager | None,
        session_id: str,
        scopes: list[str],
    ) -> None:
        if cm is None:
            await self._audit("stream_startup_dependency_missing", dependency="context_manager")
            return
        if tc.memory_store is None:
            await self._audit("stream_startup_dependency_missing", dependency="memory_store")
            return

        for scope_id in scopes:
            include_session_memories = scope_id == tc.scope_id
            await self._rehydrate_memories(
                tc,
                cm,
                session_id,
                scope_id=scope_id,
                include_session_memories=include_session_memories,
            )

    async def _list_in_progress_work_items(self) -> list[WorkItem]:
        work_item_store = self.work_item_store
        if work_item_store is None:
            await self._audit("stream_startup_dependency_missing", dependency="work_item_store")
            return []

        calls: list[Awaitable[list[WorkItem]]] = [
            work_item_store.list_by_status(status) for status in _IN_PROGRESS_STATUSES
        ]
        call_results = await asyncio.gather(*calls, return_exceptions=True)

        items_by_id: dict[str, WorkItem] = {}
        for call_result in call_results:
            if isinstance(call_result, (RuntimeError, ValueError, OSError)):
                await self._audit("work_item_rehydrate_query_failed", error=str(call_result))
                continue
            if isinstance(call_result, BaseException):
                raise call_result

            for item in call_result:
                items_by_id[item.id] = item
        return list(items_by_id.values())

    async def _restore_context_subscriptions(
        self,
        cm: LiveContextManager | None,
        in_progress_items: list[WorkItem],
    ) -> None:
        if cm is None:
            await self._audit("stream_startup_dependency_missing", dependency="context_manager")
            return
        if not in_progress_items:
            return

        restored = 0
        scope_id = self._turn_context().scope_id
        for item in in_progress_items:
            for target in self._file_subscription_targets(item):
                subscription = ContextSubscription(
                    sub_id=f"rehydrate:{item.id}:{hashlib.sha256(target.encode('utf-8')).hexdigest()[:12]}",
                    sub_type="file",
                    target=target,
                    zone=ContextZone.workspace,
                    created_at=datetime.now(UTC),
                    turn_created=self._turn_context().turn_number,
                    content_hash=hashlib.sha256(target.encode("utf-8")).hexdigest(),
                    active=True,
                    token_count=0,
                )
                cm.subscribe(scope_id, subscription)
                restored += 1

        await self._audit("context_subscriptions_restored", restored=restored)

    async def _add_rehydration_system_message(
        self,
        cm: LiveContextManager | None,
        scopes: list[str],
    ) -> None:
        if cm is None:
            await self._audit("stream_startup_dependency_missing", dependency="context_manager")
            return

        content = "[SYSTEM] Session rehydrated after restart."
        for scope_id in scopes:
            item = ContextItem(
                ctx_id=f"system:rehydrated:{scope_id}",
                zone=ContextZone.chronicle,
                content=content,
                token_count=_counter.count(content),
                created_at=datetime.now(UTC),
                turn_number=self._turn_context().turn_number,
                source="system:rehydrate",
                taint=TaintLevel.owner,
                kind="message",
            )
            cm.add(scope_id, item)
            chronicle_store = self._turn_context().chronicle_store
            if chronicle_store is not None:
                await chronicle_store.append(scope_id, item)

    async def _resume_in_progress_work_items(
        self,
        in_progress_items: list[WorkItem],
    ) -> None:
        if not in_progress_items:
            return

        executor = self._turn_context().work_executor
        if executor is None:
            await self._audit("stream_startup_dependency_missing", dependency="work_executor")
            return

        calls = [executor.execute(item) for item in in_progress_items]
        call_results = await asyncio.gather(*calls, return_exceptions=True)
        resumed = 0
        for item, call_result in zip(in_progress_items, call_results, strict=False):
            if isinstance(call_result, (RuntimeError, ValueError, OSError)):
                await self._audit(
                    "work_item_resume_failed",
                    work_item_id=item.id,
                    error=str(call_result),
                )
                continue
            if isinstance(call_result, BaseException):
                raise call_result
            resumed += 1

        await self._audit("work_items_resumed", resumed=resumed)

    async def _rehydrate_pending_proposals(self, scopes: list[str]) -> None:
        if not scopes:
            return

        await self._rehydrate_pending_batch_reviews(scopes)
        await self._rehydrate_pending_suggestions(scopes)
        await self._rehydrate_pending_autonomy_proposals(scopes)

    async def _rehydrate_pending_batch_reviews(self, scopes: list[str]) -> None:
        work_item_store = self.work_item_store
        send_batch_review = getattr(self.channel, "send_batch_review", None)
        if work_item_store is None or not callable(send_batch_review):
            return

        total = 0
        for scope_id in scopes:
            batches = await self._load_store_pending_items(
                work_item_store,
                "list_pending_batch_reviews",
                scope_id,
            )
            for batch in batches:
                await send_batch_review(self.owner_id, batch)
                total += 1
        if total:
            await self._audit("pending_batch_reviews_rehydrated", total=total)

    async def _rehydrate_pending_suggestions(self, scopes: list[str]) -> None:
        work_item_store = self.work_item_store
        send_suggestion = getattr(self.channel, "send_suggestion", None)
        if work_item_store is not None and callable(send_suggestion):
            total = 0
            for scope_id in scopes:
                pending = await self._load_store_pending_items(
                    work_item_store,
                    "list_pending_suggestions",
                    scope_id,
                )
                for suggestion in pending:
                    await send_suggestion(self.owner_id, suggestion)
                    total += 1
            if total:
                await self._audit("pending_suggestions_rehydrated", total=total)
                return

        suggestion_engine = self._get_suggestion_engine()
        if suggestion_engine is None:
            return

        total = 0
        now = datetime.now(UTC)
        for scope_id in scopes:
            pending = await suggestion_engine.generate_idle(scope_id, now)
            for suggestion in pending:
                await self._push_suggestion_to_side_panel(self.owner_id, suggestion)
                total += 1
        if total:
            await self._audit("pending_suggestions_rehydrated", total=total)

    async def _rehydrate_pending_autonomy_proposals(self, scopes: list[str]) -> None:
        work_item_store = self.work_item_store
        send_review = getattr(self.channel, "send_autonomy_threshold_review", None)
        if work_item_store is not None and callable(send_review):
            total = 0
            for scope_id in scopes:
                pending = await self._load_store_pending_items(
                    work_item_store,
                    "list_pending_autonomy_proposals",
                    scope_id,
                )
                for proposal in pending:
                    await send_review(self.owner_id, proposal)
                    total += 1
            if total:
                await self._audit("pending_autonomy_proposals_rehydrated", total=total)
                return

        autonomy_calibrator = self._get_autonomy_calibrator()
        if autonomy_calibrator is None or not callable(send_review):
            return

        total = 0
        now = datetime.now(UTC)
        for scope_id in scopes:
            pending = await autonomy_calibrator.evaluate(scope_id, now)
            for proposal in pending:
                await send_review(self.owner_id, proposal)
                total += 1
        if total:
            await self._audit("pending_autonomy_proposals_rehydrated", total=total)

    async def _load_store_pending_items(
        self,
        work_item_store: WorkItemStore,
        method_name: str,
        scope_id: str,
    ) -> list[object]:
        method = getattr(work_item_store, method_name, None)
        if not callable(method):
            return []

        try:
            maybe_result = method(scope_id)
        except TypeError:
            maybe_result = method()
        if not isinstance(maybe_result, Awaitable):
            return []

        result = await maybe_result
        if isinstance(result, list):
            return result
        return []

    async def _rehydrate_memories(
        self,
        tc: TurnContext,
        cm: LiveContextManager,
        session_id: str,
        *,
        scope_id: str,
        include_session_memories: bool,
    ) -> None:
        """Load profile state and recent session context for startup continuity."""
        profile_items = await tc.memory_store.search_keyword("user profile preferences", limit=1)
        for item in profile_items:
            cm.add(
                scope_id,
                ContextItem(
                    ctx_id=f"memory:profile:{item.memory_id}",
                    zone=ContextZone.memory,
                    content=item.content,
                    token_count=_counter.count(item.content),
                    created_at=datetime.now(UTC),
                    turn_number=tc.turn_number,
                    source="memory:profile",
                    taint=item.taint,
                    kind="memory",
                    pinned=True,
                ),
            )

        if not include_session_memories:
            return

        recent_session = await tc.memory_store.search_session(session_id)
        for item in recent_session[:10]:
            cm.add(
                scope_id,
                ContextItem(
                    ctx_id=f"memory:session:{item.memory_id}",
                    zone=ContextZone.memory,
                    content=item.content,
                    token_count=_counter.count(item.content),
                    created_at=datetime.now(UTC),
                    turn_number=tc.turn_number,
                    source="memory:session_rehydrate",
                    taint=item.taint,
                    kind="memory",
                ),
            )
