"""Tests for memory portability — export, import, round-trip."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from silas.memory.portability import MemoryPortabilityManager
from silas.models.memory import MemoryItem, MemoryType
from silas.models.messages import TaintLevel, utc_now
from silas.models.portability import SCHEMA_VERSION, MemoryBundle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    memory_id: str = "m1",
    content: str = "hello",
    taint: TaintLevel = TaintLevel.owner,
    tags: list[str] | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> MemoryItem:
    now = utc_now()
    return MemoryItem(
        memory_id=memory_id,
        content=content,
        memory_type=MemoryType.fact,
        taint=taint,
        semantic_tags=tags or [],
        created_at=created_at or now,
        updated_at=updated_at or now,
        source_kind="test",
    )


class InMemoryStore:
    """Minimal MemoryStore fake for testing portability in isolation."""

    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}

    async def store(self, item: MemoryItem) -> str:
        self._items[item.memory_id] = item
        return item.memory_id

    async def get(self, memory_id: str) -> MemoryItem | None:
        return self._items.get(memory_id)

    async def list_recent(self, limit: int) -> list[MemoryItem]:
        items = sorted(self._items.values(), key=lambda i: i.updated_at, reverse=True)
        return items[:limit]

    # Unused but required by protocol
    async def update(self, memory_id: str, **kwargs: object) -> None: ...
    async def delete(self, memory_id: str) -> None: ...
    async def search_keyword(self, query: str, limit: int) -> list[MemoryItem]:
        return []

    async def search_by_type(self, memory_type: object, limit: int) -> list[MemoryItem]:
        return []

    async def increment_access(self, memory_id: str) -> None: ...
    async def search_session(self, session_id: str) -> list[MemoryItem]:
        return []

    async def store_raw(self, item: MemoryItem) -> str:
        return await self.store(item)

    async def search_raw(self, query: str, limit: int) -> list[MemoryItem]:
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def mgr(store: InMemoryStore) -> MemoryPortabilityManager:
    return MemoryPortabilityManager(store, instance_id="test-instance")


@pytest.mark.asyncio
async def test_export_produces_valid_bundle(
    mgr: MemoryPortabilityManager, store: InMemoryStore
) -> None:
    await store.store(_make_item("m1", "first"))
    raw = await mgr.export_bundle()
    bundle = MemoryBundle.model_validate_json(raw)
    assert bundle.metadata.schema_version == SCHEMA_VERSION
    assert bundle.metadata.source_instance_id == "test-instance"
    assert bundle.metadata.item_count == 1
    assert len(bundle.items) == 1


@pytest.mark.asyncio
async def test_import_skip_keeps_existing(
    mgr: MemoryPortabilityManager, store: InMemoryStore
) -> None:
    existing = _make_item("m1", "original")
    await store.store(existing)

    bundle = await mgr.export_bundle()
    # Overwrite store content with different value so we can verify skip
    await store.store(_make_item("m1", "local-version"))

    result = await mgr.import_bundle(bundle, mode="skip")
    assert result["skipped_count"] == 1
    assert result["imported_count"] == 0
    item = await store.get("m1")
    assert item is not None
    assert item.content == "local-version"


@pytest.mark.asyncio
async def test_import_overwrite_replaces_existing(
    mgr: MemoryPortabilityManager, store: InMemoryStore
) -> None:
    await store.store(_make_item("m1", "old"))
    bundle = await mgr.export_bundle()

    await store.store(_make_item("m1", "local"))
    result = await mgr.import_bundle(bundle, mode="overwrite")
    assert result["imported_count"] == 1
    item = await store.get("m1")
    assert item is not None
    assert item.content == "old"


@pytest.mark.asyncio
async def test_import_merge_keeps_newer(
    mgr: MemoryPortabilityManager, store: InMemoryStore
) -> None:
    old_time = datetime(2025, 1, 1, tzinfo=UTC)
    new_time = datetime(2026, 1, 1, tzinfo=UTC)

    await store.store(_make_item("m1", "older", updated_at=old_time, created_at=old_time))
    # Build a bundle with the older item
    bundle_bytes = await mgr.export_bundle()

    # Now put a newer item in store
    await store.store(_make_item("m1", "newer", updated_at=new_time, created_at=new_time))

    result = await mgr.import_bundle(bundle_bytes, mode="merge")
    # Merge should keep the newer local version
    assert result["skipped_count"] == 1
    item = await store.get("m1")
    assert item is not None
    assert item.content == "newer"


@pytest.mark.asyncio
async def test_import_merge_replaces_with_newer_incoming(
    mgr: MemoryPortabilityManager, store: InMemoryStore
) -> None:
    old_time = datetime(2025, 1, 1, tzinfo=UTC)
    new_time = datetime(2026, 1, 1, tzinfo=UTC)

    await store.store(_make_item("m1", "newer-incoming", updated_at=new_time, created_at=new_time))
    bundle_bytes = await mgr.export_bundle()

    # Downgrade local to older
    await store.store(_make_item("m1", "older-local", updated_at=old_time, created_at=old_time))

    result = await mgr.import_bundle(bundle_bytes, mode="merge")
    assert result["imported_count"] == 1
    item = await store.get("m1")
    assert item is not None
    assert item.content == "newer-incoming"


@pytest.mark.asyncio
async def test_schema_version_validation(
    mgr: MemoryPortabilityManager, store: InMemoryStore
) -> None:
    await store.store(_make_item("m1"))
    raw = await mgr.export_bundle()
    # Tamper with schema version to simulate incompatible bundle
    data = json.loads(raw)
    data["metadata"]["schema_version"] = "99.0"
    tampered = json.dumps(data).encode()

    with pytest.raises(ValueError, match="Incompatible schema version"):
        await mgr.import_bundle(tampered)


@pytest.mark.asyncio
async def test_filtered_export_by_taint(
    mgr: MemoryPortabilityManager, store: InMemoryStore
) -> None:
    await store.store(_make_item("m1", taint=TaintLevel.owner))
    await store.store(_make_item("m2", taint=TaintLevel.external))

    raw = await mgr.export_bundle(filters={"taint": "owner"})
    bundle = MemoryBundle.model_validate_json(raw)
    assert len(bundle.items) == 1
    assert bundle.items[0].memory_id == "m1"


@pytest.mark.asyncio
async def test_filtered_export_by_date_range(
    mgr: MemoryPortabilityManager, store: InMemoryStore
) -> None:
    early = datetime(2025, 1, 1, tzinfo=UTC)
    late = datetime(2026, 6, 1, tzinfo=UTC)
    await store.store(_make_item("m1", created_at=early, updated_at=early))
    await store.store(_make_item("m2", created_at=late, updated_at=late))

    raw = await mgr.export_bundle(
        filters={
            "date_from": datetime(2026, 1, 1, tzinfo=UTC),
        }
    )
    bundle = MemoryBundle.model_validate_json(raw)
    assert len(bundle.items) == 1
    assert bundle.items[0].memory_id == "m2"


@pytest.mark.asyncio
async def test_filtered_export_by_tags(mgr: MemoryPortabilityManager, store: InMemoryStore) -> None:
    await store.store(_make_item("m1", tags=["important", "work"]))
    await store.store(_make_item("m2", tags=["personal"]))

    raw = await mgr.export_bundle(filters={"tags": ["important"]})
    bundle = MemoryBundle.model_validate_json(raw)
    assert len(bundle.items) == 1
    assert bundle.items[0].memory_id == "m1"


@pytest.mark.asyncio
async def test_round_trip(store: InMemoryStore) -> None:
    """Export from one store, import into another — data survives intact."""
    source_mgr = MemoryPortabilityManager(store, instance_id="source")
    items = [_make_item(f"m{i}", f"content-{i}") for i in range(5)]
    for item in items:
        await store.store(item)

    bundle_bytes = await source_mgr.export_bundle()

    # Import into a fresh store
    dest_store = InMemoryStore()
    dest_mgr = MemoryPortabilityManager(dest_store, instance_id="dest")
    result = await dest_mgr.import_bundle(bundle_bytes)

    assert result["imported_count"] == 5
    for item in items:
        got = await dest_store.get(item.memory_id)
        assert got is not None
        assert got.content == item.content
        assert got.memory_type == item.memory_type
