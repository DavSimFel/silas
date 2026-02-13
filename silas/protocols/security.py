from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class KeyManager(Protocol):
    def generate_keypair(self, owner_id: str) -> str: ...

    def sign(self, owner_id: str, payload: bytes) -> bytes: ...

    def verify(self, public_key_hex: str, payload: bytes, signature: bytes) -> tuple[bool, str]: ...


__all__ = ["KeyManager"]
