"""MemoryMixin — memory retrieval, ingestion, queries, and operations."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from silas.core.token_counter import HeuristicTokenCounter
from silas.memory.retriever import SilasMemoryRetriever
from silas.models.agents import AgentResponse, MemoryOp, MemoryOpType
from silas.models.context import ContextItem, ContextZone
from silas.models.memory import MemoryItem, MemoryType, ReingestionTier
from silas.models.messages import TaintLevel

if TYPE_CHECKING:
    from silas.core.context_manager import LiveContextManager
    from silas.core.stream._base import StreamBase
    from silas.memory.sqlite_store import SQLiteMemoryStore as MemoryStore

_counter = HeuristicTokenCounter()
_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_:-]+)")
logger = logging.getLogger(__name__)


class MemoryMixin(StreamBase if TYPE_CHECKING else object):  # type: ignore[misc]
    """Memory retrieval, ingestion, query execution, and write operations."""

    async def _auto_retrieve_memories(
        self,
        text: str,
        cm: LiveContextManager | None,
        taint: TaintLevel,
        turn_number: int,
        *,
        session_id: str | None = None,
    ) -> None:
        tc = self._turn_context()
        memory_store = tc.memory_store
        if memory_store is None or cm is None:
            return

        recalled_keyword = await memory_store.search_keyword(text, limit=3, session_id=session_id)
        recalled_entity: list[MemoryItem] = []
        mentions = self._extract_mentions(text)
        if mentions:
            entity_candidates = await memory_store.search_by_type(
                MemoryType.entity, limit=50, session_id=session_id
            )
            recalled_entity = [
                item
                for item in entity_candidates
                if self._memory_matches_any_mention(item, mentions)
            ]

        recalled_unique: dict[str, MemoryItem] = {}
        for item in [*recalled_keyword, *recalled_entity]:
            recalled_unique.setdefault(item.memory_id, item)

        for item in recalled_unique.values():
            await memory_store.increment_access(item.memory_id)
            cm.add(
                tc.scope_id,
                ContextItem(
                    ctx_id=f"memory:{item.memory_id}",
                    zone=ContextZone.memory,
                    content=item.content,
                    token_count=_counter.count(item.content),
                    created_at=datetime.now(UTC),
                    turn_number=turn_number,
                    source="memory:auto_retrieve",
                    taint=item.taint,
                    kind="memory",
                ),
            )

    async def _ingest_raw_memory(
        self, text: str, taint: TaintLevel, session_id: str, turn_number: int
    ) -> None:
        tc = self._turn_context()
        memory_store = tc.memory_store
        if memory_store is None:
            return
        await memory_store.store_raw(
            MemoryItem(
                memory_id=f"raw:{tc.scope_id}:{turn_number}:{uuid.uuid4().hex}",
                content=text,
                memory_type=MemoryType.episode,
                reingestion_tier=ReingestionTier.low_reingestion,
                taint=taint,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                session_id=session_id,
                source_kind="conversation_raw",
            ),
        )

    async def _process_memory_queries(
        self,
        response: AgentResponse | None,
        request_taint: TaintLevel,
        session_id: str,
        scope_id: str,
        cm: LiveContextManager | None,
        turn_number: int,
    ) -> list[MemoryItem]:
        """Step 9: run memory queries the agent attached to its response.

        Taint gate: external-tainted contexts must not receive owner-tainted
        memories, preventing data leakage across trust boundaries.
        """
        if response is None or not response.memory_queries:
            await self._audit("memory_queries_skipped", step=9, reason="no queries")
            return []

        tc = self._turn_context()
        memory_store = tc.memory_store
        if memory_store is None:
            await self._audit("memory_queries_skipped", step=9, reason="no memory store")
            return []

        retriever = SilasMemoryRetriever(memory_store)
        all_results: list[MemoryItem] = []

        for query in response.memory_queries:
            results = await retriever.retrieve(query, scope_id=scope_id, session_id=session_id)

            # Taint gate: strip owner memories when the request came from
            # an external context — prevents cross-boundary data leakage.
            if request_taint == TaintLevel.external:
                results = [r for r in results if r.taint != TaintLevel.owner]

            all_results.extend(results)
            await self._audit(
                "memory_query_executed",
                step=9,
                strategy=query.strategy.value,
                query=query.query,
                result_count=len(results),
            )

        # Inject retrieved memories into live context for the next turn.
        if all_results and cm is not None:
            for item in all_results:
                cm.add(
                    scope_id,
                    ContextItem(
                        ctx_id=f"mem_recall:{turn_number}:{item.memory_id}",
                        zone=ContextZone.memory,
                        content=item.content,
                        token_count=_counter.count(item.content),
                        created_at=datetime.now(UTC),
                        turn_number=turn_number,
                        source="memory:query_result",
                        taint=item.taint,
                        kind="memory",
                    ),
                )

        return all_results

    async def _process_memory_ops(
        self,
        response: AgentResponse | None,
        request_taint: TaintLevel,
        session_id: str,
        turn_number: int,
    ) -> None:
        """Step 10: execute memory write ops the agent requested.

        All ops are gated: external-tainted requests cannot write memories
        (prevents prompt-injection from persisting attacker content).
        Store/update/delete each route to the appropriate MemoryStore method.
        """
        if response is None or not response.memory_ops:
            await self._audit("memory_ops_skipped", step=10, reason="no ops")
            return

        tc = self._turn_context()
        memory_store = tc.memory_store
        if memory_store is None:
            await self._audit("memory_ops_skipped", step=10, reason="no memory store")
            return

        # Truncate excess memory ops per spec max_memory_ops_per_turn.
        _stream_cfg = getattr(tc.config, "stream", None) if tc.config is not None else None
        max_ops: int = getattr(_stream_cfg, "max_memory_ops_per_turn", 10)
        ops = response.memory_ops
        if len(ops) > max_ops:
            dropped = len(ops) - max_ops
            logger.warning(
                "Truncating memory ops from %d to %d (dropped %d)",
                len(ops),
                max_ops,
                dropped,
            )
            await self._audit(
                "memory_ops_truncated",
                step=10,
                requested=len(ops),
                allowed=max_ops,
                dropped=dropped,
            )
            ops = ops[:max_ops]

        # Hard gate: external contexts cannot write memories at all.
        if request_taint == TaintLevel.external:
            await self._audit(
                "memory_ops_blocked",
                step=10,
                reason="external taint",
                op_count=len(ops),
            )
            return

        for op in ops:
            try:
                await self._execute_single_memory_op(memory_store, op, session_id, turn_number)
                await self._audit(
                    "memory_op_executed",
                    step=10,
                    op=op.op.value,
                    memory_id=op.memory_id,
                )
            except Exception as exc:
                await self._audit(
                    "memory_op_failed",
                    step=10,
                    op=op.op.value,
                    memory_id=op.memory_id,
                    error=str(exc),
                )

    async def _execute_single_memory_op(
        self,
        memory_store: MemoryStore,
        op: MemoryOp,
        session_id: str,
        turn_number: int,
    ) -> None:
        """Dispatch a single memory op to the store."""
        if op.op == MemoryOpType.store:
            tc = self._turn_context()
            await memory_store.store(
                MemoryItem(
                    memory_id=f"agent_op:{tc.scope_id}:{turn_number}:{uuid.uuid4().hex}",
                    content=op.content or "",
                    memory_type=op.memory_type,
                    taint=TaintLevel.owner,
                    semantic_tags=op.tags,
                    entity_refs=op.entity_refs,
                    session_id=session_id,
                    source_kind="agent_memory_op",
                ),
            )
        elif op.op == MemoryOpType.update:
            assert op.memory_id is not None  # validated by MemoryOp
            await memory_store.update(op.memory_id, content=op.content)
        elif op.op == MemoryOpType.delete:
            assert op.memory_id is not None
            await memory_store.delete(op.memory_id)
        elif op.op == MemoryOpType.link:
            # Link ops update causal_refs — the lightweight graph edge.
            assert op.memory_id is not None
            assert op.link_to is not None
            existing = await memory_store.get(op.memory_id)
            if existing is not None:
                new_refs = [*existing.causal_refs, op.link_to]
                await memory_store.update(op.memory_id, causal_refs=new_refs)

    def _take_evicted_context_items(
        self,
        context_manager: LiveContextManager | None,
        scope_id: str,
    ) -> list[ContextItem]:
        if context_manager is None:
            return []
        take_last_evicted = getattr(context_manager, "take_last_evicted", None)
        if not callable(take_last_evicted):
            return []
        raw_items = take_last_evicted(scope_id)
        if not isinstance(raw_items, list):
            return []
        return [item for item in raw_items if isinstance(item, ContextItem)]

    async def _persist_evicted_context(
        self,
        evicted_items: list[ContextItem],
        session_id: str,
        turn_number: int,
    ) -> None:
        tc = self._turn_context()
        memory_store = tc.memory_store
        if memory_store is None or not evicted_items:
            return

        for item in evicted_items:
            await memory_store.store(
                MemoryItem(
                    memory_id=f"evicted:{tc.scope_id}:{turn_number}:{uuid.uuid4().hex}",
                    content=item.content,
                    memory_type=MemoryType.episode,
                    reingestion_tier=ReingestionTier.low_reingestion,
                    taint=item.taint,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    session_id=session_id,
                    source_kind="context_eviction",
                )
            )

    @staticmethod
    def _extract_mentions(message_text: str) -> set[str]:
        return {match.lstrip("@").lower() for match in _MENTION_PATTERN.findall(message_text)}

    @staticmethod
    def _memory_matches_any_mention(item: MemoryItem, mentions: set[str]) -> bool:
        if not mentions:
            return False
        content_lower = item.content.lower()
        memory_id_lower = item.memory_id.lower()
        entity_refs_lower = {ref.lstrip("@").lower() for ref in item.entity_refs}
        semantic_tags_lower = [tag.lstrip("@").lower() for tag in item.semantic_tags]
        return any(
            mention in content_lower
            or mention in memory_id_lower
            or mention in entity_refs_lower
            or any(mention in tag for tag in semantic_tags_lower)
            for mention in mentions
        )
