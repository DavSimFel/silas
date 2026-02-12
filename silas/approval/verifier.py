"""Ed25519-backed approval token issuer and verifier."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from silas.models.approval import ApprovalDecision, ApprovalScope, ApprovalToken
from silas.models.work import WorkItem
from silas.protocols.approval import NonceStore


class SilasApprovalVerifier:
    """Binds approvals to immutable plan content and enforces replay-safe consumption."""

    def __init__(
        self,
        signing_key: Ed25519PrivateKey,
        nonce_store: NonceStore,
    ) -> None:
        """Keep signing material local so only canonical payloads can authorize execution."""
        self._signing_key: Ed25519PrivateKey = signing_key
        self._public_key: Ed25519PublicKey = signing_key.public_key()
        self._nonce_store: NonceStore = nonce_store

    async def issue_token(
        self,
        work_item: WorkItem,
        decision: ApprovalDecision,
        scope: ApprovalScope = ApprovalScope.full_plan,
    ) -> ApprovalToken:
        """Mint a signed token so later verification can detect any payload tampering."""
        plan_hash: str = work_item.plan_hash()
        token_id: str = uuid4().hex
        nonce: str = uuid4().hex
        issued_at: datetime = datetime.now(UTC)
        expires_at: datetime = issued_at + timedelta(hours=1)
        max_executions: int = self._resolve_max_executions(decision.conditions)
        conditions: dict[str, object] = self._resolve_conditions(
            plan_hash=plan_hash,
            scope=scope,
            decision_conditions=decision.conditions,
        )

        canonical_bytes: bytes = self._canonical_bytes(
            plan_hash=plan_hash,
            work_item_id=work_item.id,
            scope=scope,
            verdict=decision.verdict,
            nonce=nonce,
            approval_strength=decision.approval_strength,
            issued_at=issued_at,
            expires_at=expires_at,
            max_executions=max_executions,
            conditions=conditions,
        )
        signature: bytes = self._signing_key.sign(canonical_bytes)

        return ApprovalToken(
            token_id=token_id,
            plan_hash=plan_hash,
            work_item_id=work_item.id,
            scope=scope,
            verdict=decision.verdict,
            signature=signature,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce,
            approval_strength=decision.approval_strength,
            conditions=conditions,
            max_executions=max_executions,
        )

    async def verify(
        self,
        token: ApprovalToken,
        work_item: WorkItem,
        spawned_task: WorkItem | None = None,
    ) -> tuple[bool, str]:
        """Perform consuming verification so each successful authorization is single-use tracked."""
        is_signature_valid: bool = self._verify_signature(token)
        if not is_signature_valid:
            return False, "invalid_signature"

        current_plan_hash: str = work_item.plan_hash()
        if token.plan_hash != current_plan_hash:
            return False, "plan_hash_mismatch"

        now: datetime = datetime.now(UTC)
        if now >= token.expires_at:
            return False, "token_expired"

        if token.executions_used >= token.max_executions:
            return False, "execution_limit_reached"

        if token.scope == ApprovalScope.standing:
            if spawned_task is None:
                return False, "standing_requires_spawned_task"
            if spawned_task.parent != token.work_item_id:
                return False, "standing_parent_mismatch"

        execution_nonce: str = uuid4().hex
        bound_plan_hash: str = (
            spawned_task.plan_hash() if spawned_task is not None else current_plan_hash
        )
        # Bind replay protection to token + plan context, not just raw nonce bytes.
        binding_key: str = f"{token.token_id}:{bound_plan_hash}:{execution_nonce}"
        if await self._nonce_store.is_used("exec", binding_key):
            return False, "execution_nonce_replay"

        await self._nonce_store.record("exec", binding_key)
        token.execution_nonces.append(execution_nonce)
        token.executions_used += 1
        return True, "ok"

    async def check(self, token: ApprovalToken, work_item: WorkItem) -> tuple[bool, str]:
        """Validate a previously-consumed token without consuming additional replay state."""
        is_signature_valid: bool = self._verify_signature(token)
        if not is_signature_valid:
            return False, "invalid_signature"

        if token.scope == ApprovalScope.standing:
            if work_item.parent != token.work_item_id:
                return False, "standing_parent_mismatch"
        elif token.plan_hash != work_item.plan_hash():
            return False, "plan_hash_mismatch"

        now: datetime = datetime.now(UTC)
        if now >= token.expires_at:
            return False, "token_expired"

        if token.executions_used < 1:
            return False, "token_not_consumed"
        if token.executions_used > token.max_executions:
            return False, "execution_limit_exceeded"
        return True, "ok"

    def _verify_signature(self, token: ApprovalToken) -> bool:
        canonical_bytes: bytes = self._canonical_bytes(
            plan_hash=token.plan_hash,
            work_item_id=token.work_item_id,
            scope=token.scope,
            verdict=token.verdict,
            nonce=token.nonce,
            approval_strength=token.approval_strength,
            issued_at=token.issued_at,
            expires_at=token.expires_at,
            max_executions=token.max_executions,
            conditions=token.conditions,
        )
        try:
            self._public_key.verify(token.signature, canonical_bytes)
        except (InvalidSignature, TypeError, ValueError):
            return False
        return True

    def _canonical_bytes(
        self,
        *,
        plan_hash: str,
        work_item_id: str,
        scope: ApprovalScope,
        verdict: str,
        nonce: str,
        approval_strength: str,
        issued_at: datetime,
        expires_at: datetime,
        max_executions: int,
        conditions: dict[str, object],
    ) -> bytes:
        canonical_payload: dict[str, object] = {
            "plan_hash": plan_hash,
            "work_item_id": work_item_id,
            "scope": scope.value,
            "verdict": str(verdict),
            "nonce": nonce,
            "approval_strength": approval_strength,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "max_executions": max_executions,
            "conditions": conditions,
        }
        return json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _resolve_conditions(
        self,
        *,
        plan_hash: str,
        scope: ApprovalScope,
        decision_conditions: dict[str, object],
    ) -> dict[str, object]:
        conditions: dict[str, object] = dict(decision_conditions)
        if scope == ApprovalScope.standing and "spawn_policy_hash" not in conditions:
            conditions["spawn_policy_hash"] = plan_hash
        return conditions

    def _resolve_max_executions(self, conditions: dict[str, object]) -> int:
        raw_max_executions: object | None = conditions.get("max_executions")
        if isinstance(raw_max_executions, bool):
            return 1
        if isinstance(raw_max_executions, int) and raw_max_executions > 0:
            return raw_max_executions
        return 1


__all__ = ["SilasApprovalVerifier"]
