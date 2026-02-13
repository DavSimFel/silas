from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from silas.models.skills import SkillMetadata
from silas.protocols.skills import SkillLoader
from silas.skills.hasher import SkillHasher
from silas.skills.loader import SilasSkillLoader


class SkillInstaller:
    def __init__(self, loader: SkillLoader, skills_dir: Path) -> None:
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

        # Compute hash at install time so we can detect post-install tampering.
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


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = ["SkillInstaller"]
