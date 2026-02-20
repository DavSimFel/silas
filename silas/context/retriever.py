from __future__ import annotations

from silas.core.telemetry import get_tracer
from silas.models.agents import MemoryQuery, MemoryQueryStrategy
from silas.models.memory import MemoryItem
from silas.protocols.memory import MemoryStore

_TRACER = get_tracer("silas.memory")


class SilasMemoryRetriever:
    def __init__(self, store: MemoryStore) -> None:
        """Bind a single retrieval orchestrator to a store implementation."""
        self._store = store

    async def retrieve(
        self,
        query: MemoryQuery,
        scope_id: str | None = None,
        session_id: str | None = None,
    ) -> list[MemoryItem]:
        """Apply the requested strategy and return results within the token budget."""
        with _TRACER.start_as_current_span(
            "memory.retrieve",
            attributes={
                "strategy": query.strategy.value,
                "scope_id": scope_id or "",
                "session_id": session_id or "",
                "max_results": query.max_results,
                "max_tokens": query.max_tokens,
            },
        ):
            retrieved: list[MemoryItem] = await self._dispatch_strategy(
                query=query,
                scope_id=scope_id,
                session_id=session_id,
            )
            return self._apply_token_budget(retrieved, max_tokens=query.max_tokens)

    async def _dispatch_strategy(
        self,
        query: MemoryQuery,
        scope_id: str | None,
        session_id: str | None,
    ) -> list[MemoryItem]:
        if query.strategy == MemoryQueryStrategy.keyword:
            return await self._store.search_keyword(query.query, limit=query.max_results)

        if query.strategy == MemoryQueryStrategy.temporal:
            return await self._store.list_recent(limit=query.max_results)

        if query.strategy == MemoryQueryStrategy.session:
            session_lookup: str | None = session_id or query.query.strip() or scope_id
            if session_lookup is None:
                return []
            session_results = await self._store.search_session(session_lookup)
            return session_results[: query.max_results]

        # Semantic queries first attempt explicit raw-lane retrieval and then
        # degrade to keyword search to keep recall available in non-vector mode.
        raw_results = await self._store.search_raw(query.query, limit=query.max_results)
        if raw_results:
            return raw_results
        return await self._store.search_keyword(query.query, limit=query.max_results)

    def _apply_token_budget(self, results: list[MemoryItem], max_tokens: int) -> list[MemoryItem]:
        if max_tokens <= 0:
            return []

        budgeted_results: list[MemoryItem] = []
        used_tokens = 0
        for item in results:
            estimated_tokens = len(item.content) // 4
            if used_tokens + estimated_tokens > max_tokens:
                break
            budgeted_results.append(item)
            used_tokens += estimated_tokens
        return budgeted_results


__all__ = ["SilasMemoryRetriever"]
