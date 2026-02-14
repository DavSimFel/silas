from __future__ import annotations

import asyncio
import hashlib
import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

from silas.models.memory import MemoryItem, ReingestionTier
from silas.protocols.memory import MemoryStore


class SilasMemoryConsolidator:
    def __init__(self, memory_store: MemoryStore):
        self._memory_store = memory_store

    async def run_once(self) -> dict[str, int]:
        """Provide the protocol entrypoint while preserving legacy consolidate callers.

        We execute `consolidate("")` in a worker thread so the sync compatibility
        path can safely use its internal await bridge without tripping on an
        already-running event loop.
        """
        return await asyncio.to_thread(self.consolidate, "")

    def consolidate(self, scope_id: str) -> dict[str, int]:
        now = datetime.now(UTC)
        stale_cutoff = now - timedelta(days=30)
        stats = {"merged": 0, "archived": 0, "promoted": 0}

        memories = self._load_memories(scope_id)
        if not memories:
            return stats

        deduped = self._merge_duplicates(memories, stats)

        for item in deduped.values():
            updates: dict[str, object] = {}
            if item.access_count > 10 and item.reingestion_tier != ReingestionTier.core:
                updates["reingestion_tier"] = ReingestionTier.core
                stats["promoted"] += 1
            elif (
                self._is_stale(item, stale_cutoff)
                and item.reingestion_tier != ReingestionTier.dormant
            ):
                updates["reingestion_tier"] = ReingestionTier.dormant
                stats["archived"] += 1

            if updates:
                self._await(self._memory_store.update(item.memory_id, **updates))

        return stats

    def _load_memories(self, scope_id: str) -> list[MemoryItem]:
        raw = self._await(self._memory_store.list_recent(limit=10_000))
        if not isinstance(raw, list):
            return []

        if not scope_id:
            return raw
        return [item for item in raw if item.session_id in {None, scope_id}]

    def _merge_duplicates(
        self, memories: list[MemoryItem], stats: dict[str, int]
    ) -> dict[str, MemoryItem]:
        by_hash: dict[str, list[MemoryItem]] = {}
        for item in memories:
            content_hash = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
            by_hash.setdefault(content_hash, []).append(item)

        deduped: dict[str, MemoryItem] = {}
        for group in by_hash.values():
            group.sort(
                key=lambda item: (
                    item.access_count,
                    item.updated_at,
                    item.created_at,
                    item.memory_id,
                ),
                reverse=True,
            )
            keeper = group[0]
            deduped[keeper.memory_id] = keeper

            duplicates = group[1:]
            if not duplicates:
                continue

            merged_item = keeper.model_copy(deep=True)
            for duplicate in duplicates:
                merged_item.access_count += duplicate.access_count
                merged_item.updated_at = max(merged_item.updated_at, duplicate.updated_at)
                merged_item.last_accessed = self._latest_dt(
                    merged_item.last_accessed,
                    duplicate.last_accessed,
                )
                merged_item.semantic_tags = self._merge_strings(
                    merged_item.semantic_tags,
                    duplicate.semantic_tags,
                )
                merged_item.entity_refs = self._merge_strings(
                    merged_item.entity_refs,
                    duplicate.entity_refs,
                )
                merged_item.causal_refs = self._merge_strings(
                    merged_item.causal_refs,
                    duplicate.causal_refs,
                )

                self._await(self._memory_store.delete(duplicate.memory_id))
                deduped.pop(duplicate.memory_id, None)
                stats["merged"] += 1

            self._await(
                self._memory_store.update(
                    merged_item.memory_id,
                    access_count=merged_item.access_count,
                    updated_at=merged_item.updated_at,
                    last_accessed=merged_item.last_accessed,
                    semantic_tags=merged_item.semantic_tags,
                    entity_refs=merged_item.entity_refs,
                    causal_refs=merged_item.causal_refs,
                )
            )
            deduped[merged_item.memory_id] = merged_item

        return deduped

    def _latest_dt(self, first: datetime | None, second: datetime | None) -> datetime | None:
        if first is None:
            return second
        if second is None:
            return first
        return max(first, second)

    def _merge_strings(self, first: list[str], second: list[str]) -> list[str]:
        merged = list(dict.fromkeys([*first, *second]))
        return merged

    def _is_stale(self, item: MemoryItem, stale_cutoff: datetime) -> bool:
        reference_time = item.last_accessed or item.updated_at or item.created_at
        return reference_time < stale_cutoff

    def _await(self, result: object) -> Any:
        if not inspect.isawaitable(result):
            return result

        awaitable = result
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        raise RuntimeError(
            "SilasMemoryConsolidator.consolidate() cannot await async MemoryStore inside an active event loop"
        )


__all__ = ["SilasMemoryConsolidator"]
