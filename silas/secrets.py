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


class SecretStore:
    """Unified secret store: tries OS keyring, falls back to encrypted file.

    Usage::

        store = SecretStore(data_dir=Path("./data"))
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
