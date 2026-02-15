from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

from silas.core.key_manager import SilasKeyManager
from silas.models.skill_manifest import ManifestSignature, SkillManifest


class ManifestSigner:
    """Signs and verifies manifests so runtime permissions are user-authorized."""

    def __init__(self, key_manager: SilasKeyManager) -> None:
        self._key_manager = key_manager

    def sign_manifest(self, manifest: SkillManifest, signer: str) -> SkillManifest:
        """Attach a cryptographic signature proving who approved this manifest."""
        payload: bytes = self._canonical_bytes(manifest)
        raw_signature: bytes = self._key_manager.sign(signer, payload)
        signature_b64: str = base64.b64encode(raw_signature).decode("utf-8")

        return manifest.model_copy(
            update={
                "signature": ManifestSignature(
                    signature=signature_b64,
                    signer=signer,
                    signed_at=datetime.now(UTC),
                )
            },
            deep=True,
        )

    def verify_manifest(self, manifest: SkillManifest) -> tuple[bool, str]:
        """Verify manifest integrity and signer identity before execution."""
        if manifest.signature is None:
            return False, "Manifest signature is required"

        try:
            signature_raw: bytes = base64.b64decode(
                manifest.signature.signature.encode("utf-8"),
                validate=True,
            )
        except ValueError:
            return False, "Invalid signature encoding"

        try:
            public_key: str = self._key_manager.get_public_key(manifest.signature.signer)
        except KeyError:
            return False, f"Unknown signer '{manifest.signature.signer}'"

        payload: bytes = self._canonical_bytes(manifest)
        return self._key_manager.verify(public_key, payload, signature_raw)

    def _canonical_bytes(self, manifest: SkillManifest) -> bytes:
        canonical_payload: dict[str, object] = manifest.unsigned_payload()
        return json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


__all__ = ["ManifestSigner"]
