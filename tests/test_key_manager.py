from __future__ import annotations

import pytest
from silas.core.key_manager import SilasKeyManager


class _InMemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _InMemoryKeyring:
    backend = _InMemoryKeyring()
    monkeypatch.setattr("silas.core.key_manager.keyring.set_password", backend.set_password)
    monkeypatch.setattr("silas.core.key_manager.keyring.get_password", backend.get_password)
    return backend


@pytest.fixture
def manager(fake_keyring: _InMemoryKeyring) -> SilasKeyManager:
    del fake_keyring
    return SilasKeyManager(service_name="silas-test")


def test_generate_keypair_returns_hex_public_key(manager: SilasKeyManager) -> None:
    public_key = manager.generate_keypair("owner")
    assert len(public_key) == 64
    int(public_key, 16)


def test_generate_keypair_stores_private_and_public_key(
    manager: SilasKeyManager,
    fake_keyring: _InMemoryKeyring,
) -> None:
    public_key = manager.generate_keypair("owner")

    assert ("silas-test", "owner:ed25519:private") in fake_keyring.values
    assert fake_keyring.values[("silas-test", "owner:ed25519:public")] == public_key


def test_sign_and_verify_round_trip(manager: SilasKeyManager) -> None:
    public_key = manager.generate_keypair("owner")
    payload = b"canonical-bytes"
    signature = manager.sign("owner", payload)

    valid, reason = manager.verify(public_key, payload, signature)
    assert valid is True
    assert reason == "Valid"


def test_verify_rejects_tampered_payload(manager: SilasKeyManager) -> None:
    public_key = manager.generate_keypair("owner")
    signature = manager.sign("owner", b"payload-a")

    valid, reason = manager.verify(public_key, b"payload-b", signature)
    assert valid is False
    assert reason == "Invalid signature"


def test_verify_rejects_modified_signature(manager: SilasKeyManager) -> None:
    public_key = manager.generate_keypair("owner")
    signature = bytearray(manager.sign("owner", b"payload"))
    signature[-1] ^= 0x01

    valid, reason = manager.verify(public_key, b"payload", bytes(signature))
    assert valid is False
    assert reason == "Invalid signature"


def test_verify_rejects_invalid_public_key_encoding(manager: SilasKeyManager) -> None:
    valid, reason = manager.verify("not-hex", b"payload", b"sig")
    assert valid is False
    assert "encoding" in reason.lower()


def test_verify_rejects_invalid_public_key_length(manager: SilasKeyManager) -> None:
    valid, reason = manager.verify("aa", b"payload", b"sig")
    assert valid is False
    assert "length" in reason.lower()


def test_sign_raises_when_key_is_missing(manager: SilasKeyManager) -> None:
    with pytest.raises(KeyError):
        manager.sign("missing-owner", b"payload")


def test_get_public_key_returns_stored_value(manager: SilasKeyManager) -> None:
    public_key = manager.generate_keypair("owner")
    assert manager.get_public_key("owner") == public_key


def test_rotated_keypair_invalidates_old_signature(manager: SilasKeyManager) -> None:
    first_public_key = manager.generate_keypair("owner")
    signature = manager.sign("owner", b"payload")
    manager.generate_keypair("owner")

    valid, reason = manager.verify(first_public_key, b"payload", signature)
    assert valid is True
    assert reason == "Valid"

    current_public_key = manager.get_public_key("owner")
    current_valid, _ = manager.verify(current_public_key, b"payload", signature)
    assert current_valid is False
