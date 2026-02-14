"""SigningMixin — message signing, verification, and taint resolution."""

from __future__ import annotations

import hashlib
import hmac
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from silas.models.messages import SignedMessage, TaintLevel


class SigningMixin:
    """Message signing, verification, and inbound taint resolution."""

    async def verify_inbound(self, signed_message: SignedMessage) -> tuple[bool, str]:
        """Check whether an inbound message is trustworthy.

        Trust model: The channel layer is responsible for authenticating senders
        (e.g. validating a WebSocket session token). The stream does NOT self-sign
        messages — that would be circular (signing and verifying with the same key
        always passes). Instead we check:

        1. Did the channel authenticate this sender? (is_authenticated flag)
        2. If a client-side signature is present (future), verify it with the
           client's public key and consume the nonce for replay protection.

        When client-side signing is added, this method will also verify Ed25519
        signatures against registered client public keys — not the stream's own key.
        """
        msg = signed_message.message

        # Future: if signature is non-empty, verify against client public key + nonce.
        # For now, trust depends entirely on channel authentication.
        if msg.is_authenticated:
            return True, "authenticated_session"

        return False, "no_client_signature"

    async def _prepare_signed_inbound_message(
        self,
        *,
        message: object,
        processed_message_text: str,
        turn_number: int,
    ) -> SignedMessage:
        """Build a SignedMessage using channel-level trust, not self-signing.

        The old approach signed messages with the stream's own key then verified
        with the same key — a tautology that always passed. Now trust derives from
        the channel's authentication state (set before the message reaches the stream).
        """
        signed = self._create_inbound_signed_message(message, processed_message_text)
        is_verified, verify_reason = await self.verify_inbound(signed)
        if not is_verified:
            await self._audit(
                "inbound_message_untrusted",
                turn_number=turn_number,
                sender_id=message.sender_id,
                reason=verify_reason,
            )
        taint = self._resolve_inbound_taint(message.sender_id, is_verified)
        return signed.model_copy(update={"taint": taint})

    def _create_inbound_signed_message(
        self,
        message: object,
        processed_message_text: str,
    ) -> SignedMessage:
        """Wrap an inbound message without self-signing.

        Signature is empty — the stream must not sign-then-verify its own messages.
        When client-side crypto is added, the client will provide the signature and
        nonce; the stream will only verify. The Ed25519 key infrastructure is kept
        for that future use and for signing outbound attestations.
        """
        message_payload = message.model_copy(update={"text": processed_message_text})
        return SignedMessage(
            message=message_payload,
            signature=b"",
            nonce=uuid.uuid4().hex,
            taint=TaintLevel.external,
        )

    def _sign_payload(self, canonical_payload: bytes) -> bytes:
        """Sign a payload with the stream's key (used for outbound attestations, not inbound)."""
        signing_key = self._signing_key
        if isinstance(signing_key, Ed25519PrivateKey):
            return signing_key.sign(canonical_payload)
        if isinstance(signing_key, bytes):
            return hmac.new(signing_key, canonical_payload, hashlib.sha256).digest()
        raise RuntimeError("stream signing key is not configured")

    def _is_valid_signature(self, canonical_payload: bytes, signature: bytes) -> bool:
        """Verify a signature against the stream's key (kept for outbound/future client use)."""
        signing_key = self._signing_key
        if isinstance(signing_key, Ed25519PrivateKey):
            try:
                signing_key.public_key().verify(signature, canonical_payload)
            except (InvalidSignature, TypeError, ValueError):
                return False
            return True
        if isinstance(signing_key, bytes):
            expected_signature = hmac.new(signing_key, canonical_payload, hashlib.sha256).digest()
            return hmac.compare_digest(expected_signature, signature)
        return False

    def _resolve_inbound_taint(self, sender_id: str, is_verified: bool) -> TaintLevel:
        """Determine trust level from channel authentication and sender identity.

        Owner taint requires BOTH: the channel authenticated the sender AND the
        sender_id matches the stream owner. This prevents privilege escalation
        from authenticated-but-non-owner users.
        """
        if is_verified and sender_id == self.owner_id:
            return TaintLevel.owner
        return TaintLevel.external
