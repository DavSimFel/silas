from __future__ import annotations

from typing import Protocol, runtime_checkable

from silas.models.execution import ExecutionEnvelope, ExecutionResult, Sandbox, SandboxConfig


@runtime_checkable
class EphemeralExecutor(Protocol):
    async def execute(self, envelope: ExecutionEnvelope) -> ExecutionResult: ...


@runtime_checkable
class SandboxManager(Protocol):
    async def create(self, config: SandboxConfig) -> Sandbox: ...

    async def destroy(self, sandbox_id: str) -> None: ...


@runtime_checkable
class KeyManager(Protocol):
    def generate_keypair(self, owner_id: str) -> str: ...

    def sign(self, owner_id: str, payload: bytes) -> bytes: ...

    def verify(self, public_key_hex: str, payload: bytes, signature: bytes) -> tuple[bool, str]: ...


__all__ = ["EphemeralExecutor", "KeyManager", "SandboxManager"]
