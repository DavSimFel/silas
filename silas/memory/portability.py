"""MemoryPortability implementation — export/import memory bundles across instances."""

from __future__ import annotations

from datetime import datetime

from silas.models.memory import MemoryItem
from silas.models.messages import TaintLevel
from silas.models.portability import (
    SCHEMA_VERSION,
    BundleMetadata,
    ImportResult,
    MemoryBundle,
)
from silas.protocols.memory import MemoryStore


class MemoryPortabilityManager:
    """Standalone service wrapping any MemoryStore with export/import capabilities.

    Satisfies the MemoryPortability protocol (bytes in/out) while using typed
    Pydantic models internally for safety.
    """

    def __init__(self, store: MemoryStore, instance_id: str = "") -> None:
        self._store = store
        self._instance_id = instance_id

    # -- Protocol surface (bytes-oriented) ------------------------------------

    async def export_bundle(
        self,
        since: datetime | None = None,
        include_raw: bool = True,
        filters: dict[str, object] | None = None,
    ) -> bytes:
        """Serialize matching memories into a portable JSON blob."""
        bundle = await self._build_bundle(since=since, include_raw=include_raw, filters=filters)
        return bundle.model_dump_json(indent=2).encode()

    async def import_bundle(
        self,
        bundle: bytes,
        mode: str = "merge",
    ) -> dict[str, object]:
        """Deserialize a bundle and upsert into the backing store."""
        parsed = MemoryBundle.model_validate_json(bundle)
        result = await self._apply_bundle(parsed, conflict_strategy=mode)
        return result.model_dump()

    # -- Typed helpers (usable directly in-process) ---------------------------

    async def _build_bundle(
        self,
        *,
        since: datetime | None = None,
        include_raw: bool = True,
        filters: dict[str, object] | None = None,
    ) -> MemoryBundle:
        # Grab a broad set; real vector/FTS filtering isn't needed for export
        items = await self._store.list_recent(limit=10_000)

        if since is not None:
            items = [i for i in items if i.updated_at >= since]

        if filters:
            items = _apply_filters(items, filters)

        if not include_raw:
            # Strip embeddings to shrink bundle size
            for item in items:
                item.embedding = None

        metadata = BundleMetadata(
            source_instance_id=self._instance_id,
            schema_version=SCHEMA_VERSION,
            item_count=len(items),
        )
        return MemoryBundle(metadata=metadata, items=items)

    async def _apply_bundle(
        self,
        bundle: MemoryBundle,
        conflict_strategy: str = "skip",
    ) -> ImportResult:
        _validate_schema_version(bundle.metadata.schema_version)

        result = ImportResult()
        for item in bundle.items:
            existing = await self._store.get(item.memory_id)

            if existing is None:
                await self._store.store(item)
                result.imported_count += 1
                continue

            # Conflict detected — apply chosen strategy
            result.conflict_count += 1
            if conflict_strategy == "skip":
                result.skipped_count += 1
            elif conflict_strategy == "overwrite":
                await self._store.store(item)
                result.imported_count += 1
            elif conflict_strategy == "merge":
                # "merge" keeps whichever was updated more recently
                winner = item if item.updated_at > existing.updated_at else existing
                if winner is item:
                    await self._store.store(item)
                    result.imported_count += 1
                else:
                    result.skipped_count += 1
            else:
                result.errors.append(f"Unknown conflict strategy: {conflict_strategy}")

        return result


# -- Private helpers ----------------------------------------------------------


def _apply_filters(items: list[MemoryItem], filters: dict[str, object]) -> list[MemoryItem]:
    """Narrow items by optional taint, date_from, date_until, tags."""
    out = items

    if "taint" in filters:
        level = TaintLevel(str(filters["taint"]))
        out = [i for i in out if i.taint == level]

    if "date_from" in filters:
        dt = filters["date_from"]
        if isinstance(dt, datetime):
            out = [i for i in out if i.created_at >= dt]

    if "date_until" in filters:
        dt = filters["date_until"]
        if isinstance(dt, datetime):
            out = [i for i in out if i.created_at <= dt]

    if "tags" in filters:
        required = set(filters["tags"]) if isinstance(filters["tags"], list) else {filters["tags"]}
        out = [i for i in out if required & set(i.semantic_tags)]

    return out


def _validate_schema_version(version: str) -> None:
    """Reject bundles from incompatible major versions."""
    major = version.split(".")[0]
    current_major = SCHEMA_VERSION.split(".")[0]
    if major != current_major:
        msg = f"Incompatible schema version {version} (current: {SCHEMA_VERSION})"
        raise ValueError(msg)


__all__ = ["MemoryPortabilityManager"]
