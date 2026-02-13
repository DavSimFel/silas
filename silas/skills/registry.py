from __future__ import annotations

import logging
from pathlib import Path

from silas.models.skills import SkillDefinition
from silas.skills.hasher import SkillHasher

logger = logging.getLogger(__name__)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        self._skills[skill.name] = skill.model_copy(deep=True)

    def get(self, name: str) -> SkillDefinition | None:
        skill = self._skills.get(name)
        if skill is None:
            return None
        return skill.model_copy(deep=True)

    def list_all(self) -> list[SkillDefinition]:
        return [
            self._skills[name].model_copy(deep=True)
            for name in sorted(self._skills)
        ]

    def has(self, name: str) -> bool:
        return name in self._skills

    def store_hash(self, name: str, skill_path: Path) -> str:
        """Compute and persist the hash for an installed skill.

        Called at install time so we have a baseline to compare against
        on every subsequent load.
        """
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"skill '{name}' not in registry")
        content_hash = SkillHasher.compute_hash(skill_path)
        updated = skill.model_copy(update={"verified_hash": content_hash})
        self._skills[name] = updated
        return content_hash

    def verify_hash(self, name: str, skill_path: Path) -> bool:
        """Re-compute hash and compare to stored value.

        Returns True if the skill is unmodified. Logs a security event
        and returns False on mismatch so the caller can block activation.
        """
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"skill '{name}' not in registry")

        if skill.verified_hash is None:
            # First load — no stored hash yet; caller should store one.
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


__all__ = ["SkillRegistry"]
