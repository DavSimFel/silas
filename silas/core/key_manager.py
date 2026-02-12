from __future__ import annotations

import base64

import keyring
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_PRIVATE_KEY_LABEL = "ed25519:private"
_PUBLIC_KEY_LABEL = "ed25519:public"


class SilasKeyManager:
    """Stores signing keys in the OS keyring and exposes Ed25519 operations."""

    def __init__(self, service_name: str = "silas") -> None:
        self._service_name = service_name

    def generate_keypair(self, owner_id: str) -> str:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        private_key_raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_key_raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        public_key_hex = public_key_raw.hex()

        self._set_secret(owner_id, _PRIVATE_KEY_LABEL, base64.b64encode(private_key_raw).decode("utf-8"))
        self._set_secret(owner_id, _PUBLIC_KEY_LABEL, public_key_hex)
        return public_key_hex

    def sign(self, owner_id: str, payload: bytes) -> bytes:
        private_key = self._load_private_key(owner_id)
        return private_key.sign(payload)

    def verify(self, public_key_hex: str, payload: bytes, signature: bytes) -> tuple[bool, str]:
        try:
            public_key_raw = bytes.fromhex(public_key_hex)
        except ValueError:
            return False, "Invalid public key encoding"

        if len(public_key_raw) != 32:
            return False, "Invalid public key length"

        try:
            public_key = Ed25519PublicKey.from_public_bytes(public_key_raw)
            public_key.verify(signature, payload)
        except InvalidSignature:
            return False, "Invalid signature"
        except Exception as exc:  # pragma: no cover - backend and key errors vary by platform
            return False, str(exc)

        return True, "Valid"

    def get_public_key(self, owner_id: str) -> str:
        public_key_hex = self._get_secret(owner_id, _PUBLIC_KEY_LABEL)
        if public_key_hex is not None:
            return public_key_hex

        private_key = self._load_private_key(owner_id)
        derived_public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return derived_public_key.hex()

    def _load_private_key(self, owner_id: str) -> Ed25519PrivateKey:
        encoded_private_key = self._get_secret(owner_id, _PRIVATE_KEY_LABEL)
        if encoded_private_key is None:
            raise KeyError(f"no private key found for owner '{owner_id}'")

        try:
            private_key_raw = base64.b64decode(encoded_private_key.encode("utf-8"), validate=True)
        except Exception as exc:
            raise ValueError("stored private key is not valid base64") from exc

        try:
            return Ed25519PrivateKey.from_private_bytes(private_key_raw)
        except Exception as exc:
            raise ValueError("stored private key has invalid format") from exc

    def _credential_name(self, owner_id: str, label: str) -> str:
        return f"{owner_id}:{label}"

    def _set_secret(self, owner_id: str, label: str, value: str) -> None:
        keyring.set_password(
            self._service_name,
            self._credential_name(owner_id, label),
            value,
        )

    def _get_secret(self, owner_id: str, label: str) -> str | None:
        return keyring.get_password(
            self._service_name,
            self._credential_name(owner_id, label),
        )


__all__ = ["SilasKeyManager"]
