"""Unified ContextRegistry for the new context pipeline."""

from __future__ import annotations

from datetime import UTC, datetime

from silas.models.context_item import ContextItem


class ContextRegistry:
    """In-memory registry of :class:`ContextItem` objects keyed by *item_id*."""

    def __init__(self) -> None:
        self._items: dict[str, ContextItem] = {}

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def upsert(self, item: ContextItem) -> None:
        """Insert or update *item* by ``item_id``, setting *last_modified* to now."""
        item.last_modified = datetime.now(UTC)
        self._items[item.item_id] = item

    def remove(self, item_id: str) -> bool:
        """Remove an item. Returns ``True`` if it existed."""
        return self._items.pop(item_id, None) is not None

    def touch(self, item_id: str) -> None:
        """Bump *last_modified* without changing content."""
        item = self._items.get(item_id)
        if item is not None:
            item.last_modified = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, item_id: str) -> ContextItem | None:
        return self._items.get(item_id)

    def query(
        self,
        source_prefix: str = "",
        role: str | None = None,
        tags: set[str] | None = None,
    ) -> list[ContextItem]:
        results: list[ContextItem] = []
        for item in self._items.values():
            if source_prefix and not item.source.startswith(source_prefix):
                continue
            if role is not None and item.role != role:
                continue
            if tags is not None and not tags.issubset(item.tags):
                continue
            results.append(item)
        return results

    # ------------------------------------------------------------------
    # Budget helpers
    # ------------------------------------------------------------------

    def budget_usage(self) -> dict[str, int]:
        """Token counts grouped by *source_tag*."""
        usage: dict[str, int] = {}
        for item in self._items.values():
            tag = item.source_tag or ""
            usage[tag] = usage.get(tag, 0) + item.token_count
        return usage

    def total_tokens(self) -> int:
        return sum(item.token_count for item in self._items.values())

    def count(self) -> int:
        return len(self._items)

    # ------------------------------------------------------------------
    # Render (read-only view)
    # ------------------------------------------------------------------

    def render(
        self,
        role: str,
        budget_tokens: int,
        budget_caps: dict[str, float] | None = None,
    ) -> list[ContextItem]:
        """Return items for *role* that fit within *budget_tokens*.

        * Conversation messages (``source`` starting with ``"message:"``)
          maintain chronological order; other items are sorted by
          *last_modified* descending.
        * Per-source-tag caps limit how many tokens each tag may consume.
        * Eviction order: expired TTL → lowest *eviction_priority* → oldest
          *last_modified*.

        This method does **not** mutate the registry.
        """
        now = datetime.now(UTC)
        caps = budget_caps or {}

        candidates = [item for item in self._items.values() if item.role == role]

        # Partition into messages vs non-messages
        messages = [c for c in candidates if c.source.startswith("message:")]
        others = [c for c in candidates if not c.source.startswith("message:")]

        # Sort: messages chronologically, others by last_modified desc
        messages.sort(key=lambda i: i.last_modified)
        others.sort(key=lambda i: i.last_modified, reverse=True)

        # Eviction: sort candidates for selection (keep best, skip worst)
        def _eviction_key(item: ContextItem) -> tuple[bool, float, datetime]:
            expired = (
                item.ttl_seconds is not None
                and (now - item.last_modified).total_seconds() > item.ttl_seconds
            )
            return (not expired, item.eviction_priority, item.last_modified)

        # Select items within budget
        tag_usage: dict[str, int] = {}
        total_used = 0
        selected_ids: set[str] = set()

        # Process others first (sorted best-first by eviction key desc)
        others_ranked = sorted(others, key=_eviction_key, reverse=True)
        for item in others_ranked:
            tag = item.source_tag or ""
            tag_cap = caps.get(tag)
            tag_limit = int(budget_tokens * tag_cap) if tag_cap is not None else budget_tokens

            current_tag = tag_usage.get(tag, 0)
            if current_tag + item.token_count > tag_limit:
                continue
            if total_used + item.token_count > budget_tokens:
                continue

            tag_usage[tag] = current_tag + item.token_count
            total_used += item.token_count
            selected_ids.add(item.item_id)

        # Process messages (chronological, same budget logic)
        for item in messages:
            tag = item.source_tag or ""
            tag_cap = caps.get(tag)
            tag_limit = int(budget_tokens * tag_cap) if tag_cap is not None else budget_tokens

            current_tag = tag_usage.get(tag, 0)
            if current_tag + item.token_count > tag_limit:
                continue
            if total_used + item.token_count > budget_tokens:
                continue

            tag_usage[tag] = current_tag + item.token_count
            total_used += item.token_count
            selected_ids.add(item.item_id)

        # Build final list: others (last_modified desc) then messages (chronological)
        result = [i for i in others if i.item_id in selected_ids]
        result.sort(key=lambda i: i.last_modified, reverse=True)
        result.extend(i for i in messages if i.item_id in selected_ids)

        return result

    def evict(
        self,
        budget_tokens: int,
        budget_caps: dict[str, float] | None = None,
    ) -> list[ContextItem]:
        """Remove items until total tokens ≤ *budget_tokens*. Returns evicted items.

        Eviction order: expired TTL first, then lowest *eviction_priority*,
        then oldest *last_modified*.
        """
        now = datetime.now(UTC)
        evicted: list[ContextItem] = []

        while self.total_tokens() > budget_tokens and self._items:
            # Pick worst candidate
            worst = min(
                self._items.values(),
                key=lambda i: (
                    # expired items go first (False < True)
                    not (
                        i.ttl_seconds is not None
                        and (now - i.last_modified).total_seconds() > i.ttl_seconds
                    ),
                    i.eviction_priority,
                    i.last_modified,
                ),
            )
            self._items.pop(worst.item_id)
            evicted.append(worst)

        return evicted


__all__ = ["ContextRegistry"]
