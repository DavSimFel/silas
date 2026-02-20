from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from silas.models.messages import TaintLevel
from silas.models.skills import SkillDefinition, SkillMetadata
from silas.models.work import WorkItem
from silas.protocols.skills import SkillLoader as SkillLoaderProtocol
from silas.gates.taint import TaintTracker
from silas.tools.skill_toolset import (
    SkillToolset,
    ToolCallResult,
    ToolDefinition,
    ToolsetProtocol,
)

logger = logging.getLogger(__name__)

_TAINT_LEVEL_MAP: dict[str, TaintLevel] = {
    "owner": TaintLevel.owner,
    "auth": TaintLevel.auth,
    "external": TaintLevel.external,
}

_EXCLUDED_DIRS: frozenset[str] = frozenset({"__pycache__", ".git"})
_INCLUDED_EXTENSIONS: frozenset[str] = frozenset({".py", ".md", ".yaml", ".yml", ".toml", ".json"})
_FORBIDDEN_PATTERNS: tuple[str, ...] = ("eval(", "exec(", "__import__")
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class SkillHasher:
    """Computes a deterministic content hash over a skill directory."""

    @staticmethod
    def compute_hash(skill_path: Path) -> str:
        """Return hex SHA-256 of all relevant files under *skill_path*."""
        resolved = skill_path.resolve()
        if not resolved.is_dir():
            raise ValueError(f"skill path is not a directory: {skill_path}")

        hasher = hashlib.sha256()
        files = sorted(_iter_hashable_files(resolved), key=lambda p: str(p.relative_to(resolved)))

        for file_path in files:
            rel = str(file_path.relative_to(resolved))
            hasher.update(rel.encode("utf-8"))
            hasher.update(file_path.read_bytes())

        return hasher.hexdigest()


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        self._skills[skill.name] = skill.model_copy(deep=True)
        if skill.taint_level is not None:
            taint = _TAINT_LEVEL_MAP[skill.taint_level]
            TaintTracker.add_tool_taint(skill.name, taint)

    def get(self, name: str) -> SkillDefinition | None:
        skill = self._skills.get(name)
        if skill is None:
            return None
        return skill.model_copy(deep=True)

    def list_all(self) -> list[SkillDefinition]:
        return [self._skills[name].model_copy(deep=True) for name in sorted(self._skills)]

    def has(self, name: str) -> bool:
        return name in self._skills

    def store_hash(self, name: str, skill_path: Path) -> str:
        """Compute and persist the hash for an installed skill."""
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"skill '{name}' not in registry")
        content_hash = SkillHasher.compute_hash(skill_path)
        updated = skill.model_copy(update={"verified_hash": content_hash})
        self._skills[name] = updated
        return content_hash

    def verify_hash(self, name: str, skill_path: Path) -> bool:
        """Re-compute hash and compare to stored value."""
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"skill '{name}' not in registry")

        if skill.verified_hash is None:
            return True

        current_hash = SkillHasher.compute_hash(skill_path)
        if current_hash != skill.verified_hash:
            logger.warning(
                "SECURITY: skill '%s' hash mismatch — expected %s, got %s. "
                "Skill files were modified after installation. Blocking activation.",
                name,
                skill.verified_hash,
                current_hash,
            )
            return False
        return True


class SecurityError(Exception):
    """Raised when a skill fails hash integrity verification."""


class SilasSkillLoader:
    def __init__(self, skills_dir: str | Path) -> None:
        self._skills_dir = Path(skills_dir).expanduser().resolve()

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    def scan(self) -> list[SkillMetadata]:
        if not self._skills_dir.exists():
            return []

        metadata: list[SkillMetadata] = []
        for skill_file in sorted(self._skills_dir.glob("*/SKILL.md")):
            skill_name = skill_file.parent.name
            try:
                metadata.append(self.load_metadata(skill_name))
            except ValueError:
                continue
        return metadata

    def load_metadata(self, skill_name: str) -> SkillMetadata:
        skill_file = self._skill_markdown_path(skill_name)
        parsed = self._parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        try:
            metadata = SkillMetadata.model_validate(parsed)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid frontmatter for '{skill_name}': {exc}") from exc

        if metadata.name != skill_name:
            raise ValueError(
                f"frontmatter name '{metadata.name}' does not match skill directory '{skill_name}'"
            )

        return metadata

    def verify_integrity(
        self,
        skill_name: str,
        stored_hash: str | None,
    ) -> tuple[bool, str]:
        """Check skill files against a previously stored hash."""
        skill_dir = self._resolve_skill_dir(skill_name)
        current_hash = SkillHasher.compute_hash(skill_dir)

        if stored_hash is None:
            return True, current_hash

        if current_hash != stored_hash:
            raise SecurityError(
                f"Skill '{skill_name}' failed integrity check: "
                f"expected hash {stored_hash}, got {current_hash}. "
                "Re-approval required."
            )

        return True, current_hash

    def load(self, skill_name: str, *, stored_hash: str | None = None) -> tuple[str, str]:
        """Load skill content after verifying integrity."""
        _ok, current_hash = self.verify_integrity(skill_name, stored_hash)
        content = self.load_full(skill_name)
        return content, current_hash

    def load_full(self, skill_name: str) -> str:
        skill_file = self._skill_markdown_path(skill_name)
        return skill_file.read_text(encoding="utf-8")

    def resolve_script(self, skill_name: str, script_path: str) -> str:
        if not script_path.strip():
            raise ValueError("script_path must be non-empty")

        skill_dir = self._resolve_skill_dir(skill_name)
        candidate = (skill_dir / script_path).resolve()
        if not _is_within(candidate, skill_dir):
            raise ValueError("script path escapes skill directory")
        if not candidate.exists() or not candidate.is_file():
            raise ValueError(f"script not found: {script_path}")
        return str(candidate)

    def validate(self, skill_name: str) -> dict[str, object]:
        errors: list[str] = []
        warnings: list[str] = []

        try:
            skill_dir = self._resolve_skill_dir(skill_name)
        except ValueError as exc:
            return {"valid": False, "errors": [str(exc)], "warnings": warnings}

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            return {"valid": False, "errors": ["SKILL.md not found"], "warnings": warnings}

        frontmatter: dict[str, object] = {}
        try:
            frontmatter = self._parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            errors.append(str(exc))

        metadata = self._coerce_metadata(frontmatter, skill_name, errors)

        if metadata.name != skill_name:
            errors.append(
                f"frontmatter name '{metadata.name}' does not match skill directory '{skill_name}'"
            )

        errors.extend(validate_frontmatter(metadata))
        errors.extend(validate_scripts(skill_dir))
        errors.extend(check_forbidden_patterns(skill_dir))
        errors.extend(validate_references(skill_dir, metadata))

        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    def import_external(self, source: str, format_hint: str | None = None) -> dict[str, object]:
        payload = self._parse_external_payload(source)
        hint = (format_hint or "").strip().lower()

        normalized: dict[str, object] | None = None
        detected = ""

        if hint in {"", "openai"}:
            normalized = self._normalize_openai(payload)
            if normalized is not None:
                detected = "openai"

        if normalized is None and hint in {"", "claude", "tool_use"}:
            normalized = self._normalize_claude(payload)
            if normalized is not None:
                detected = "claude"

        if normalized is None:
            raise ValueError("unsupported external skill format")

        skill_md = self._render_skill_md(normalized)
        return {
            "format": detected,
            "skill_name": normalized["name"],
            "skill_md": skill_md,
            "transformation_report": {
                "source_format": detected,
                "translated_fields": ["name", "description", "script_args"],
                "removed_fields": [],
                "warnings": [],
            },
        }

    def _resolve_skill_dir(self, skill_name: str) -> Path:
        if not skill_name.strip():
            raise ValueError("skill_name must be non-empty")

        skill_dir = (self._skills_dir / skill_name).resolve()
        if not _is_within(skill_dir, self._skills_dir):
            raise ValueError("skill path escapes skills directory")
        if not skill_dir.exists() or not skill_dir.is_dir():
            raise ValueError(f"skill not found: {skill_name}")
        return skill_dir

    def _skill_markdown_path(self, skill_name: str) -> Path:
        skill_dir = self._resolve_skill_dir(skill_name)
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists() or not skill_file.is_file():
            raise ValueError(f"SKILL.md not found for skill '{skill_name}'")
        return skill_file

    def _parse_frontmatter(self, skill_content: str) -> dict[str, object]:
        lines = skill_content.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("SKILL.md is missing YAML frontmatter start delimiter")

        end_idx = -1
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_idx = idx
                break

        if end_idx == -1:
            raise ValueError("SKILL.md is missing YAML frontmatter end delimiter")

        block = "\n".join(lines[1:end_idx])
        try:
            parsed = yaml.safe_load(block) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML frontmatter: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("SKILL.md frontmatter must be a mapping")

        return parsed

    def _coerce_metadata(
        self,
        frontmatter: dict[str, object],
        skill_name: str,
        errors: list[str],
    ) -> SkillMetadata:
        try:
            return SkillMetadata.model_validate(frontmatter)
        except (ValueError, TypeError) as exc:
            errors.append(f"invalid frontmatter schema: {exc}")

        name = frontmatter.get("name")
        description = frontmatter.get("description")
        activation = frontmatter.get("activation")
        ui = frontmatter.get("ui")
        composes_with = frontmatter.get("composes_with")
        script_args = frontmatter.get("script_args")
        metadata = frontmatter.get("metadata")
        requires_approval = frontmatter.get("requires_approval")
        tool_name = frontmatter.get("tool_name")
        tool_description = frontmatter.get("tool_description")
        tool_schema = frontmatter.get("tool_schema")

        return SkillMetadata(
            name=name if isinstance(name, str) else skill_name,
            description=description if isinstance(description, str) else "",
            activation=activation if isinstance(activation, str) else None,
            ui=ui if isinstance(ui, dict) else {},
            composes_with=composes_with if isinstance(composes_with, list) else [],
            script_args=script_args if isinstance(script_args, dict) else {},
            metadata=metadata if isinstance(metadata, dict) else {},
            requires_approval=requires_approval if isinstance(requires_approval, bool) else False,
            tool_name=tool_name if isinstance(tool_name, str) else None,
            tool_description=tool_description if isinstance(tool_description, str) else None,
            tool_schema=tool_schema if isinstance(tool_schema, dict) else {},
        )

    def _parse_external_payload(self, source: str) -> object:
        try:
            return json.loads(source)
        except json.JSONDecodeError:
            try:
                parsed = yaml.safe_load(source)
            except yaml.YAMLError as exc:
                raise ValueError(f"invalid external skill source: {exc}") from exc
            if parsed is None:
                raise ValueError("external source is empty") from None
            return parsed

    def _normalize_openai(self, payload: object) -> dict[str, object] | None:
        function_payload = _extract_openai_function_payload(payload)
        if function_payload is None:
            return None

        name = _normalize_skill_name(str(function_payload.get("name") or "imported-skill"))
        description = _normalize_description(
            str(function_payload.get("description") or "Imported OpenAI function skill.")
        )
        parameters = function_payload.get("parameters")
        script_args = _schema_to_script_args(parameters)
        return {"name": name, "description": description, "script_args": script_args}

    def _normalize_claude(self, payload: object) -> dict[str, object] | None:
        tool_payload = _extract_claude_tool_payload(payload)
        if tool_payload is None:
            return None

        name = _normalize_skill_name(str(tool_payload.get("name") or "imported-skill"))
        description = _normalize_description(
            str(tool_payload.get("description") or "Imported Claude tool skill.")
        )
        input_schema = tool_payload.get("input_schema")
        script_args = _schema_to_script_args(input_schema)
        return {"name": name, "description": description, "script_args": script_args}

    def _render_skill_md(self, normalized: dict[str, object]) -> str:
        frontmatter: dict[str, object] = {
            "name": normalized["name"],
            "description": normalized["description"],
            "activation": "manual",
            "requires_approval": False,
        }
        script_args = normalized.get("script_args")
        if isinstance(script_args, dict) and script_args:
            frontmatter["script_args"] = script_args

        yaml_block = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        title = str(normalized["name"]).replace("-", " ").title()
        return (
            f"---\n{yaml_block}\n---\n\n# {title}\n\nImported skill scaffold generated by Silas.\n"
        )


SkillLoader = SilasSkillLoader


class ToolDeclaration(BaseModel):
    """Single tool exposed by the skill."""

    name: str
    description: str = ""


class SkillManifest(BaseModel):
    """Schema for the skill package manifest (skill.yaml / skill.json)."""

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
        """Import a skill from a package directory."""
        source_path = Path(source).resolve()
        if not source_path.is_dir():
            raise SkillImportError(f"skill source is not a directory: {source}")

        manifest = self._load_manifest(source_path)

        skill = SkillDefinition(
            name=manifest.name,
            description=manifest.description,
            version=manifest.version,
            taint_level=manifest.taint_level,
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
        """Adapt a skill definition to the local environment."""
        description = _substitute_env_vars(skill.description, env)

        if manifest is not None:
            missing = [dep for dep in manifest.dependencies if not _is_dependency_available(dep)]
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


class SkillInstaller:
    def __init__(self, loader: SkillLoaderProtocol, skills_dir: Path) -> None:
        self._loader = loader
        self._skills_dir = skills_dir.expanduser().resolve()

    def install(self, source: str, approval_token: str | None = None) -> dict[str, object]:
        source_dir = self._resolve_source_dir(source)
        source_loader = SilasSkillLoader(source_dir.parent)
        skill_name = source_dir.name
        validation_report = source_loader.validate(skill_name)

        metadata: SkillMetadata | None = None
        try:
            metadata = source_loader.load_metadata(skill_name)
        except ValueError as exc:
            validation_report = dict(validation_report)
            errors = list(validation_report.get("errors", []))
            errors.append(str(exc))
            validation_report["errors"] = errors
            validation_report["valid"] = False

        if not bool(validation_report.get("valid")):
            return {
                "installed": False,
                "skill_name": skill_name,
                "approval_required": False,
                "validation_report": validation_report,
            }

        if metadata is not None and metadata.requires_approval and approval_token is None:
            return {
                "installed": False,
                "skill_name": skill_name,
                "approval_required": True,
                "validation_report": validation_report,
            }

        destination = self._resolve_destination(skill_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source_dir, destination)

        content_hash = SkillHasher.compute_hash(destination)

        indexed = self._loader.scan()
        return {
            "installed": True,
            "skill_name": skill_name,
            "approval_required": False,
            "validation_report": validation_report,
            "indexed_count": len(indexed),
            "destination": str(destination),
            "installed_at": datetime.now(UTC).isoformat(),
            "verified_hash": content_hash,
        }

    def uninstall(self, skill_name: str) -> bool:
        try:
            target = self._resolve_destination(skill_name)
        except ValueError:
            return False

        if not target.exists() or not target.is_dir():
            return False

        shutil.rmtree(target)
        return True

    def list_installed(self) -> list[SkillMetadata]:
        return self._loader.scan()

    def _resolve_source_dir(self, source: str) -> Path:
        candidate = Path(source).expanduser().resolve()
        if candidate.is_file() and candidate.name == "SKILL.md":
            candidate = candidate.parent
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError(f"source skill directory not found: {source}")
        if not (candidate / "SKILL.md").is_file():
            raise ValueError(f"source skill is missing SKILL.md: {source}")
        return candidate

    def _resolve_destination(self, skill_name: str) -> Path:
        if not skill_name.strip():
            raise ValueError("skill_name must be non-empty")

        destination = (self._skills_dir / skill_name).resolve()
        if not _is_within(destination, self._skills_dir):
            raise ValueError("skill destination escapes skills directory")
        return destination


def validate_frontmatter(metadata: SkillMetadata) -> list[str]:
    errors: list[str] = []

    if not metadata.name.strip():
        errors.append("frontmatter.name is required")

    description = metadata.description.strip()
    if not description:
        errors.append("frontmatter.description is required")
    elif len(description) < 10 or len(description) > 500:
        errors.append("frontmatter.description must be between 10 and 500 characters")

    return errors


def validate_scripts(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    for script_file in _iter_python_files(skill_dir):
        source = script_file.read_text(encoding="utf-8")
        try:
            compile(source, str(script_file), "exec")
        except SyntaxError as exc:
            line = exc.lineno or 0
            rel = script_file.relative_to(skill_dir)
            errors.append(f"syntax error in {rel}:{line}: {exc.msg}")
    return errors


def check_forbidden_patterns(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    for script_file in _iter_python_files(skill_dir):
        rel = script_file.relative_to(skill_dir)
        lines = script_file.read_text(encoding="utf-8").splitlines()
        for line_no, line in enumerate(lines, start=1):
            for pattern in _FORBIDDEN_PATTERNS:
                if pattern in line:
                    errors.append(f"forbidden pattern '{pattern}' in {rel}:{line_no}")
    return errors


def validate_references(skill_dir: Path, metadata: SkillMetadata) -> list[str]:
    errors: list[str] = []
    skill_root = skill_dir.resolve()
    for script_path in metadata.script_args:
        candidate = (skill_root / script_path).resolve()
        if not _is_within(candidate, skill_root):
            errors.append(f"script_args reference '{script_path}' escapes skill directory")
            continue
        if not candidate.exists() or not candidate.is_file():
            errors.append(f"script_args reference '{script_path}' does not exist")
    return errors


@dataclass(slots=True)
class ResolvedSkills:
    """Container for the resolution result — metadata + names actually found."""

    metadata: list[SkillMetadata] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


ParentSkillResolver = Callable[[WorkItem], list[str]]


class PreparedToolset:
    """Wraps a toolset with role-based preparation."""

    def __init__(self, inner: ToolsetProtocol, agent_role: str) -> None:
        self._inner = inner
        self.agent_role = agent_role

    def list_tools(self) -> list[ToolDefinition]:
        return self._inner.list_tools()

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        return self._inner.call(tool_name, arguments)


class FilteredToolset:
    """Only exposes tools in the allowed_tools list."""

    def __init__(self, inner: ToolsetProtocol, allowed_tools: list[str]) -> None:
        self._inner = inner
        self._allowed: set[str] = set(allowed_tools)

    def list_tools(self) -> list[ToolDefinition]:
        return [t for t in self._inner.list_tools() if t.name in self._allowed]

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        if tool_name not in self._allowed:
            return ToolCallResult(status="filtered", error=f"tool '{tool_name}' not in allowlist")
        return self._inner.call(tool_name, arguments)


class ApprovalRequiredToolset:
    """Intercepts calls to tools that require approval."""

    def __init__(self, inner: ToolsetProtocol) -> None:
        self._inner = inner
        self._approval_required: set[str] = {
            t.name for t in inner.list_tools() if t.requires_approval
        }

    def list_tools(self) -> list[ToolDefinition]:
        return self._inner.list_tools()

    def call(self, tool_name: str, arguments: dict[str, object]) -> ToolCallResult:
        if tool_name in self._approval_required:
            return ToolCallResult(
                status="approval_required",
                error=f"tool '{tool_name}' requires approval before execution",
            )
        return self._inner.call(tool_name, arguments)


class SkillResolver:
    """Resolves skill names to metadata and builds execution toolsets."""

    def __init__(
        self,
        loader: SilasSkillLoader,
        parent_resolver: ParentSkillResolver | None = None,
    ) -> None:
        self._loader = loader
        self._parent_resolver = parent_resolver

    def resolve_for_work_item(self, work_item: WorkItem) -> list[SkillMetadata]:
        """Resolve the work item's skill names to loaded SkillMetadata."""
        skill_names = list(work_item.skills) if work_item.skills else []

        if not skill_names and self._parent_resolver is not None:
            skill_names = self._parent_resolver(work_item)

        resolved = self._resolve_names(skill_names)

        if resolved.missing:
            logger.warning(
                "Work item %s: missing skills %s",
                work_item.id,
                resolved.missing,
            )

        return resolved.metadata

    def prepare_toolset(
        self,
        work_item: WorkItem,
        agent_role: str,
        base_toolset: ToolsetProtocol,
        allowed_tools: list[str],
    ) -> ApprovalRequiredToolset:
        """Build the canonical wrapper chain for work item execution."""
        skill_metadata = self.resolve_for_work_item(work_item)

        skill_toolset = SkillToolset(
            base_toolset=base_toolset,
            skill_metadata=skill_metadata,
        )

        prepared = PreparedToolset(inner=skill_toolset, agent_role=agent_role)
        filtered = FilteredToolset(inner=prepared, allowed_tools=allowed_tools)

        return ApprovalRequiredToolset(inner=filtered)

    def _resolve_names(self, skill_names: list[str]) -> ResolvedSkills:
        metadata: list[SkillMetadata] = []
        missing: list[str] = []

        for name in skill_names:
            try:
                meta = self._loader.load_metadata(name)
                metadata.append(meta)
            except ValueError:
                missing.append(name)

        return ResolvedSkills(metadata=metadata, missing=missing)


def _iter_hashable_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        if any(part in _EXCLUDED_DIRS for part in item.relative_to(root).parts):
            continue
        if item.suffix == ".pyc":
            continue
        if item.suffix in _INCLUDED_EXTENSIONS:
            result.append(item)
    return result


def _iter_python_files(skill_dir: Path) -> list[Path]:
    if not skill_dir.exists():
        return []
    return sorted(path for path in skill_dir.rglob("*.py") if path.is_file())


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _extract_openai_function_payload(payload: object) -> dict[str, object] | None:
    if isinstance(payload, dict):
        if "function" in payload and isinstance(payload["function"], dict):
            return payload["function"]
        if "name" in payload and "parameters" in payload:
            return payload
        tools = payload.get("tools")
        if isinstance(tools, list):
            for item in tools:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "function" and isinstance(item.get("function"), dict):
                    return item["function"]

    if isinstance(payload, list):
        for item in payload:
            extracted = _extract_openai_function_payload(item)
            if extracted is not None:
                return extracted
    return None


def _extract_claude_tool_payload(payload: object) -> dict[str, object] | None:
    if isinstance(payload, dict):
        if "name" in payload and "input_schema" in payload:
            return payload
        tools = payload.get("tools")
        if isinstance(tools, list):
            for item in tools:
                if not isinstance(item, dict):
                    continue
                if "name" in item and "input_schema" in item:
                    return item

    if isinstance(payload, list):
        for item in payload:
            extracted = _extract_claude_tool_payload(item)
            if extracted is not None:
                return extracted
    return None


def _schema_to_script_args(schema: object) -> dict[str, dict[str, object]]:
    if not isinstance(schema, dict):
        return {}

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}

    normalized_properties: dict[str, object] = {}
    for key, value in properties.items():
        if isinstance(key, str) and isinstance(value, dict):
            normalized_properties[key] = value

    if not normalized_properties:
        return {}

    return {"scripts/run.py": normalized_properties}


def _normalize_skill_name(raw: str) -> str:
    lowered = raw.strip().lower()
    cleaned = re.sub(r"[^a-z0-9-]+", "-", lowered)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "imported-skill"


def _normalize_description(raw: str) -> str:
    text = raw.strip()
    if len(text) < 10:
        return "Imported external skill for tool execution."
    if len(text) > 500:
        return text[:500]
    return text


def _substitute_env_vars(text: str, env: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return env.get(var_name, match.group(0))

    return _ENV_VAR_RE.sub(_replace, text)


def _is_dependency_available(dep: str) -> bool:
    return shutil.which(dep) is not None


__all__ = [
    "ApprovalRequiredToolset",
    "DependencyError",
    "FilteredToolset",
    "ParentSkillResolver",
    "PreparedToolset",
    "ResolvedSkills",
    "SecurityError",
    "SilasSkillLoader",
    "SkillHasher",
    "SkillImportError",
    "SkillImporter",
    "SkillInstaller",
    "SkillLoader",
    "SkillManifest",
    "SkillRegistry",
    "SkillResolver",
    "ToolDeclaration",
    "check_forbidden_patterns",
    "validate_frontmatter",
    "validate_references",
    "validate_scripts",
]
