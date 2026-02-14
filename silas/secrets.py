"""Secret storage with OS keyring primary and encrypted-file fallback.

Implements §0.5 secret isolation: credentials stored via opaque ref_id,
never in config files or LLM context. Headless environments without a
system keyring fall back to a Fernet-encrypted JSON file keyed by
machine-id.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

logger = logging.getLogger(__name__)

_SERVICE_NAME = "silas"


class SecretBackend(Protocol):
    """Protocol for pluggable secret backends."""

    def get(self, ref_id: str) -> str | None:
        """Retrieve a secret by ref_id. Returns None if not found."""
        ...

    def set(self, ref_id: str, value: str) -> None:
        """Store a secret under ref_id."""
        ...

    def delete(self, ref_id: str) -> None:
        """Remove a secret by ref_id. No-op if absent."""
        ...


class KeyringBackend:
    """OS keyring via the ``keyring`` library."""

    def get(self, ref_id: str) -> str | None:
        import keyring  # lazy import — may not be available

        return keyring.get_password(_SERVICE_NAME, ref_id)

    def set(self, ref_id: str, value: str) -> None:
        import keyring

        keyring.set_password(_SERVICE_NAME, ref_id, value)

    def delete(self, ref_id: str) -> None:
        import contextlib

        import keyring

        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(_SERVICE_NAME, ref_id)


class EncryptedFileBackend:
    """Fernet-encrypted JSON file, keyed by machine-id.

    Suitable for headless servers where no system keyring is available.
    The encryption key is derived from ``/etc/machine-id`` (Linux),
    ``IOPlatformUUID`` (macOS), or hostname + OS as last resort.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fernet = Fernet(self._derive_key())

    def get(self, ref_id: str) -> str | None:
        store = self._load()
        return store.get(ref_id)

    def set(self, ref_id: str, value: str) -> None:
        store = self._load()
        store[ref_id] = value
        self._save(store)

    def delete(self, ref_id: str) -> None:
        store = self._load()
        if ref_id in store:
            del store[ref_id]
            self._save(store)

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            ct = self._path.read_bytes()
            pt = self._fernet.decrypt(ct)
            data = json.loads(pt)
        except (InvalidToken, json.JSONDecodeError, OSError):
            logger.warning("Encrypted secret store corrupted or unreadable — starting fresh")
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save(self, store: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        pt = json.dumps(store, sort_keys=True).encode()
        ct = self._fernet.encrypt(pt)
        self._path.write_bytes(ct)

    @staticmethod
    def _derive_key() -> bytes:
        """Derive a Fernet key from the machine identity.

        Not meant to resist a determined local attacker — just ensures
        secrets aren't plain-text on disk and can't be read after
        migrating the data dir to a different machine.
        """
        raw = _get_machine_id()
        # SHA-256 → 32 bytes → url-safe base64 = valid Fernet key
        digest = hashlib.sha256(raw.encode()).digest()
        return urlsafe_b64encode(digest)


def _get_machine_id() -> str:
    """Best-effort stable machine identifier."""
    # Linux: /etc/machine-id
    machine_id_path = Path("/etc/machine-id")
    if machine_id_path.exists():
        mid = machine_id_path.read_text().strip()
        if mid:
            return mid

    # macOS: IOPlatformUUID via ioreg
    if platform.system() == "Darwin":
        import subprocess

        try:
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2]
        except (OSError, subprocess.TimeoutExpired):
            pass

    # Fallback: hostname + OS (weak but stable)
    return f"{platform.node()}-{platform.system()}-{os.getuid()}"


class PassphraseBackend:
    """Fernet-encrypted file keyed by a user-provided passphrase.

    Tier 2 storage: requires explicit user action to unlock.  The
    passphrase is stretched via PBKDF2-HMAC-SHA256 (600 000 iterations)
    with a persistent salt.  Used for Ed25519 signing keys and other
    high-sensitivity material that must not auto-unlock.
    """

    _ITERATIONS = 600_000

    def __init__(self, path: Path, passphrase: str) -> None:
        self._path = path
        self._salt_path = path.with_suffix(".salt")
        self._fernet = Fernet(self._derive_key(passphrase))

    def get(self, ref_id: str) -> str | None:
        store = self._load()
        return store.get(ref_id)

    def set(self, ref_id: str, value: str) -> None:
        store = self._load()
        store[ref_id] = value
        self._save(store)

    def delete(self, ref_id: str) -> None:
        store = self._load()
        if ref_id in store:
            del store[ref_id]
            self._save(store)

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            ct = self._path.read_bytes()
            pt = self._fernet.decrypt(ct)
            data = json.loads(pt)
        except (InvalidToken, json.JSONDecodeError, OSError):
            logger.warning("Tier-2 store corrupted or wrong passphrase")
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save(self, store: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        pt = json.dumps(store, sort_keys=True).encode()
        ct = self._fernet.encrypt(pt)
        self._path.write_bytes(ct)

    def _derive_key(self, passphrase: str) -> bytes:
        """PBKDF2 stretch the passphrase into a Fernet key."""
        import hashlib as _hashlib

        salt = self._load_or_create_salt()
        dk = _hashlib.pbkdf2_hmac(
            "sha256",
            passphrase.encode(),
            salt,
            self._ITERATIONS,
        )
        return urlsafe_b64encode(dk)

    def _load_or_create_salt(self) -> bytes:
        if self._salt_path.exists():
            return self._salt_path.read_bytes()
        salt = os.urandom(32)
        self._salt_path.parent.mkdir(parents=True, exist_ok=True)
        self._salt_path.write_bytes(salt)
        return salt


class SecretStore:
    """Tier 1 — auto-unlock secret store for API keys and service tokens.

    Tries OS keyring, falls back to machine-id-encrypted file.
    Secrets stored here are accessible without user interaction.

    Usage::

        store = SecretStore(data_dir=settings.data_dir)
        store.set("openrouter-api-key", "sk-or-...")
        key = store.get("openrouter-api-key")
    """

    def __init__(self, data_dir: Path) -> None:
        self._backend = self._select_backend(data_dir)

    def get(self, ref_id: str) -> str | None:
        """Retrieve secret by opaque ref_id."""
        return self._backend.get(ref_id)

    def set(self, ref_id: str, value: str) -> None:
        """Store secret under opaque ref_id."""
        self._backend.set(ref_id, value)

    def delete(self, ref_id: str) -> None:
        """Delete secret by ref_id."""
        self._backend.delete(ref_id)

    @property
    def backend_name(self) -> str:
        return type(self._backend).__name__

    @staticmethod
    def _select_backend(data_dir: Path) -> SecretBackend:
        """Pick OS keyring if available, else encrypted file."""
        try:
            import keyring
            from keyring.backends.fail import Keyring as FailKeyring

            kr = keyring.get_keyring()
            if not isinstance(kr, FailKeyring):
                logger.info("Using OS keyring backend: %s", type(kr).__name__)
                return KeyringBackend()
        except ImportError:
            pass

        file_path = data_dir / ".secrets.enc"
        logger.info("No OS keyring available — using encrypted file backend: %s", file_path)
        return EncryptedFileBackend(file_path)


class SigningKeyStore:
    """Tier 2 — passphrase-protected store for Ed25519 signing keys.

    Requires a user-provided passphrase to unlock.  The passphrase is
    obtained from (in order):

    1. Explicit ``passphrase`` argument (CLI ``silas start --passphrase``)
    2. ``SILAS_SIGNING_PASSPHRASE`` environment variable (headless/CI)
    3. Interactive prompt at startup

    The signing key never leaves this store in plaintext — callers receive
    an ``Ed25519PrivateKey`` object that stays in process memory only.
    """

    _PRIVATE_KEY_REF = "ed25519:private"
    _PUBLIC_KEY_REF = "ed25519:public"

    def __init__(self, data_dir: Path, passphrase: str) -> None:
        path = data_dir / ".signing.enc"
        self._backend = PassphraseBackend(path, passphrase)

    def has_keypair(self) -> bool:
        """Check if a signing keypair exists."""
        return self._backend.get(self._PRIVATE_KEY_REF) is not None

    def generate_keypair(self) -> str:
        """Generate and store a new Ed25519 keypair.  Returns public key hex."""
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        private_raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

        self._backend.set(self._PRIVATE_KEY_REF, base64.b64encode(private_raw).decode())
        self._backend.set(self._PUBLIC_KEY_REF, public_raw.hex())
        return public_raw.hex()

    def load_private_key(self) -> Ed25519PrivateKey:
        """Load the Ed25519 private key from the passphrase-protected store."""
        import base64

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        encoded = self._backend.get(self._PRIVATE_KEY_REF)
        if encoded is None:
            raise KeyError("no signing key found — run 'silas init' first")
        raw = base64.b64decode(encoded.encode(), validate=True)
        return Ed25519PrivateKey.from_private_bytes(raw)

    def get_public_key_hex(self) -> str:
        """Load the public key hex string."""
        pub_hex = self._backend.get(self._PUBLIC_KEY_REF)
        if pub_hex is None:
            # Derive from private key
            pk = self.load_private_key()
            from cryptography.hazmat.primitives import serialization

            raw = pk.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            return raw.hex()
        return pub_hex


def load_stream_signing_key(data_dir: Path, passphrase: str) -> Ed25519PrivateKey:
    """Load the stream signing key and fail closed when trust material is missing.

    Why: stream message tainting depends on this key. Startup must abort if the
    runtime cannot unlock Tier-2 signing material, rather than silently downgrading.
    """
    store = SigningKeyStore(data_dir, passphrase)
    if not store.has_keypair():
        raise RuntimeError("no signing keypair found — run `silas init` first")
    try:
        return store.load_private_key()
    except (KeyError, ValueError) as exc:
        raise RuntimeError("unable to unlock signing key — check signing passphrase") from exc
