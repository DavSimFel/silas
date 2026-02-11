from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from silas.models.skills import SkillMetadata
from silas.skills.validator import (
    check_forbidden_patterns,
    validate_frontmatter,
    validate_references,
    validate_scripts,
)


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
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"invalid frontmatter for '{skill_name}': {exc}") from exc

        if metadata.name != skill_name:
            raise ValueError(
                f"frontmatter name '{metadata.name}' does not match skill directory '{skill_name}'"
            )

        return metadata

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
        except Exception as exc:  # noqa: BLE001
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
            f"---\n{yaml_block}\n---\n\n"
            f"# {title}\n\n"
            "Imported skill scaffold generated by Silas.\n"
        )


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


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


SkillLoader = SilasSkillLoader


__all__ = ["SilasSkillLoader", "SkillLoader"]
