from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from silas.core.stream._nonce import _InMemoryNonceStore


@pytest.mark.asyncio
async def test_nonce_replay_detection_in_same_domain() -> None:
    store = _InMemoryNonceStore()
    nonce = "nonce-1"

    assert await store.is_used("msg", nonce) is False
    await store.record("msg", nonce)
    assert await store.is_used("msg", nonce) is True


@pytest.mark.asyncio
async def test_nonce_uniqueness_for_generated_nonce_values() -> None:
    store = _InMemoryNonceStore()
    nonces = [uuid.uuid4().hex for _ in range(10)]

    for nonce in nonces:
        assert await store.is_used("msg", nonce) is False
        await store.record("msg", nonce)

    for nonce in nonces:
        assert await store.is_used("msg", nonce) is True


@pytest.mark.asyncio
async def test_nonce_domain_scoping_prevents_cross_domain_collision() -> None:
    store = _InMemoryNonceStore()
    nonce = "shared-value"

    await store.record("msg", nonce)

    assert await store.is_used("msg", nonce) is True
    assert await store.is_used("exec", nonce) is False


@pytest.mark.asyncio
async def test_prune_expired_is_deterministic_noop() -> None:
    store = _InMemoryNonceStore()
    await store.record("msg", "n")

    removed = await store.prune_expired(datetime.now(UTC))

    assert removed == 0
    assert await store.is_used("msg", "n") is True
