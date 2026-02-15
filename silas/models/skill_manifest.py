from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class ManifestPermissions(BaseModel):
    """Defines the least-privilege capability set a skill is allowed to use."""

    shell_commands: list[str] = Field(default_factory=list)
    network_hosts: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    file_paths: list[str] = Field(default_factory=list)


class ManifestSignature(BaseModel):
    """Carries the detached signature metadata required for manifest trust."""

    signature: str
    signer: str
    signed_at: datetime

    @field_validator("signed_at")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("signed_at must be timezone-aware")
        return value


class SkillManifest(BaseModel):
    """Represents a signed permission contract used to constrain skill execution."""

    name: str
    version: str
    permissions: ManifestPermissions = Field(default_factory=ManifestPermissions)
    signature: ManifestSignature | None = None

    @classmethod
    def from_yaml(cls, content: str) -> SkillManifest:
        """Parse manifest YAML text so skills can be loaded from disk deterministically."""
        try:
            parsed: object = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid manifest YAML: {exc}") from exc

        if not isinstance(parsed, dict):
            raise ValueError("manifest YAML must decode to a mapping")

        return cls.model_validate(parsed)

    @classmethod
    def from_yaml_file(cls, manifest_path: str | Path) -> SkillManifest:
        """Load and parse ``manifest.yaml`` from disk in one call for runtime usage."""
        path = Path(manifest_path)
        return cls.from_yaml(path.read_text(encoding="utf-8"))

    def unsigned_payload(self) -> dict[str, object]:
        """Return canonical signable data excluding the mutable signature envelope."""
        return self.model_dump(mode="json", exclude={"signature"})


__all__ = ["ManifestPermissions", "ManifestSignature", "SkillManifest"]
