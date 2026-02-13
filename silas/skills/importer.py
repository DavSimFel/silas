"""Skill import and adaptation system (§10.4).

External skills are packaged as directories containing a manifest
(skill.yaml or skill.json) plus code files.  The importer validates
the manifest, optionally adapts the skill to the local environment
(resolving env vars, checking deps), computes an integrity hash for
INV-06, and returns a SkillDefinition ready for registry insertion.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from silas.models.skills import SkillDefinition
from silas.skills.hasher import SkillHasher

# Pattern for ${ENV_VAR} placeholders in skill config values
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ToolDeclaration(BaseModel):
    """Single tool exposed by the skill."""

    name: str
    description: str = ""


class SkillManifest(BaseModel):
    """Schema for the skill package manifest (skill.yaml / skill.json).

    Kept strict so malformed packages fail fast at import time rather
    than producing subtle runtime errors.
    """

    name: str
    version: str
    description: str
    author: str = ""
    tools: list[ToolDeclaration] = Field(default_factory=list)
    taint_level: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    entry_point: str = "main.py"

    @field_validator("taint_level")
    @classmethod
    def _validate_taint_level(cls, value: str | None) -> str | None:
        if value is not None and value not in ("owner", "auth", "external"):
            raise ValueError("taint_level must be 'owner', 'auth', or 'external'")
        return value


class SkillImportError(Exception):
    """Raised when a skill package cannot be imported."""


class DependencyError(SkillImportError):
    """Raised when required dependencies are unavailable."""


class SkillImporter:
    """Import external skill packages into the Silas runtime."""

    def import_skill(
        self,
        source: str | Path,
        *,
        adapt: bool = True,
        env: dict[str, str] | None = None,
    ) -> SkillDefinition:
        """Import a skill from a package directory.

        Parses the manifest, validates it, optionally adapts to the local
        environment, computes an integrity hash (INV-06), and returns a
        SkillDefinition ready for registry insertion.
        """
        source_path = Path(source).resolve()
        if not source_path.is_dir():
            raise SkillImportError(f"skill source is not a directory: {source}")

        manifest = self._load_manifest(source_path)

        skill = SkillDefinition(
            name=manifest.name,
            description=manifest.description,
            version=manifest.version,
            taint_level=manifest.taint_level,
            # Hash the package contents for tamper detection (INV-06)
            verified_hash=SkillHasher.compute_hash(source_path),
        )

        if adapt:
            skill = self.adapt_skill(skill, env or {}, manifest=manifest)

        return skill

    def adapt_skill(
        self,
        skill: SkillDefinition,
        env: dict[str, str],
        *,
        manifest: SkillManifest | None = None,
    ) -> SkillDefinition:
        """Adapt a skill definition to the local environment.

        - Replaces ${ENV_VAR} placeholders in description
        - Validates required dependencies are available on PATH
        - Propagates taint_level from manifest declarations
        """
        description = _substitute_env_vars(skill.description, env)

        # Dependency availability check — fail early so the user knows
        # what's missing before the skill is ever activated.
        if manifest is not None:
            missing = [
                dep for dep in manifest.dependencies
                if not _is_dependency_available(dep)
            ]
            if missing:
                raise DependencyError(
                    f"missing dependencies for skill '{skill.name}': {', '.join(missing)}"
                )

        return skill.model_copy(update={"description": description})

    def _load_manifest(self, source_path: Path) -> SkillManifest:
        """Find and parse the manifest file from the skill package."""
        yaml_path = source_path / "skill.yaml"
        json_path = source_path / "skill.json"

        raw: dict[str, Any]
        if yaml_path.exists():
            text = yaml_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(text)
            if not isinstance(parsed, dict):
                raise SkillImportError("skill.yaml must contain a mapping")
            raw = parsed
        elif json_path.exists():
            text = json_path.read_text(encoding="utf-8")
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise SkillImportError("skill.json must contain a mapping")
        else:
            raise SkillImportError(
                f"no manifest found in {source_path} (expected skill.yaml or skill.json)"
            )

        try:
            return SkillManifest.model_validate(raw)
        except (ValueError, TypeError) as exc:
            raise SkillImportError(f"invalid skill manifest: {exc}") from exc


def _substitute_env_vars(text: str, env: dict[str, str]) -> str:
    """Replace ${VAR} placeholders with values from env dict."""

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return env.get(var_name, match.group(0))

    return _ENV_VAR_RE.sub(_replace, text)


def _is_dependency_available(dep: str) -> bool:
    """Check whether a dependency is available (on PATH as executable)."""
    return shutil.which(dep) is not None


__all__ = [
    "DependencyError",
    "SkillImportError",
    "SkillImporter",
    "SkillManifest",
    "ToolDeclaration",
]
