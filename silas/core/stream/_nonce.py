"""Ephemeral nonce store for replay protection."""

from __future__ import annotations

from datetime import datetime


class _InMemoryNonceStore:
    """Ephemeral replay guard for local/test streams.

    Why: production stream startup injects ``SQLiteNonceStore``. This fallback keeps
    direct unit-test ``Stream(...)`` construction replay-safe without requiring a DB.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def is_used(self, domain: str, nonce: str) -> bool:
        return f"{domain}:{nonce}" in self._seen

    async def record(self, domain: str, nonce: str) -> None:
        self._seen.add(f"{domain}:{nonce}")

    async def prune_expired(self, older_than: datetime) -> int:
        del older_than
        return 0
