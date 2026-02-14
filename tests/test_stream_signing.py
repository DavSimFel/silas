from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from silas.core.stream._signing import SigningMixin
from silas.models.messages import ChannelMessage, SignedMessage, TaintLevel


class _SigningHarness(SigningMixin):
    def __init__(self, signing_key: object, owner_id: str = "owner") -> None:
        self._signing_key = signing_key
        self.owner_id = owner_id
        self.audit_events: list[tuple[str, dict[str, Any]]] = []

    async def _audit(self, event: str, **kwargs: Any) -> None:
        self.audit_events.append((event, kwargs))


def _message(*, sender_id: str = "owner", is_authenticated: bool = True) -> ChannelMessage:
    return ChannelMessage(
        channel="web",
        sender_id=sender_id,
        text="hello",
        timestamp=datetime.now(UTC),
        is_authenticated=is_authenticated,
    )


@pytest.mark.asyncio
async def test_verify_inbound_authenticated_message_is_trusted() -> None:
    harness = _SigningHarness(signing_key=b"secret")
    signed = SignedMessage(message=_message(is_authenticated=True), signature=b"", nonce="n1")

    verified, reason = await harness.verify_inbound(signed)

    assert verified is True
    assert reason == "authenticated_session"


@pytest.mark.asyncio
async def test_verify_inbound_unauthenticated_message_is_untrusted() -> None:
    harness = _SigningHarness(signing_key=b"secret")
    signed = SignedMessage(message=_message(is_authenticated=False), signature=b"", nonce="n1")

    verified, reason = await harness.verify_inbound(signed)

    assert verified is False
    assert reason == "no_client_signature"


def test_create_inbound_signed_message_keeps_processed_text_without_self_signature() -> None:
    harness = _SigningHarness(signing_key=b"secret")
    signed = harness._create_inbound_signed_message(_message(), "processed")

    assert signed.message.text == "processed"
    assert signed.signature == b""
    assert len(signed.nonce) == 32
    assert signed.taint == TaintLevel.external


@pytest.mark.asyncio
async def test_prepare_signed_inbound_message_owner_taint_when_verified_owner() -> None:
    harness = _SigningHarness(signing_key=b"secret", owner_id="owner")
    message = _message(sender_id="owner", is_authenticated=True)

    signed = await harness._prepare_signed_inbound_message(
        message=message,
        processed_message_text="processed",
        turn_number=7,
    )

    assert signed.message.text == "processed"
    assert signed.taint == TaintLevel.owner
    assert harness.audit_events == []


@pytest.mark.asyncio
async def test_prepare_signed_inbound_message_external_taint_and_audit_when_untrusted() -> None:
    harness = _SigningHarness(signing_key=b"secret", owner_id="owner")
    message = _message(sender_id="owner", is_authenticated=False)

    signed = await harness._prepare_signed_inbound_message(
        message=message,
        processed_message_text="processed",
        turn_number=9,
    )

    assert signed.taint == TaintLevel.external
    assert harness.audit_events == [
        (
            "inbound_message_untrusted",
            {"turn_number": 9, "sender_id": "owner", "reason": "no_client_signature"},
        )
    ]


def test_sign_and_verify_payload_with_ed25519_key() -> None:
    harness = _SigningHarness(signing_key=Ed25519PrivateKey.generate())
    payload = b"canonical-payload"

    signature = harness._sign_payload(payload)

    assert harness._is_valid_signature(payload, signature) is True


def test_ed25519_invalid_signature_is_rejected() -> None:
    harness = _SigningHarness(signing_key=Ed25519PrivateKey.generate())
    payload = b"canonical-payload"
    invalid_signature = Ed25519PrivateKey.generate().sign(payload)

    assert harness._is_valid_signature(payload, invalid_signature) is False


def test_sign_and_verify_payload_with_hmac_key() -> None:
    harness = _SigningHarness(signing_key=b"shared-secret")
    payload = b"canonical-payload"

    signature = harness._sign_payload(payload)

    assert harness._is_valid_signature(payload, signature) is True
    assert harness._is_valid_signature(payload + b"-tampered", signature) is False


def test_sign_payload_raises_when_key_missing() -> None:
    harness = _SigningHarness(signing_key=None)

    with pytest.raises(RuntimeError, match="not configured"):
        harness._sign_payload(b"payload")


def test_verify_signature_returns_false_for_unsupported_key_type() -> None:
    harness = _SigningHarness(signing_key=object())

    assert harness._is_valid_signature(b"payload", b"signature") is False
