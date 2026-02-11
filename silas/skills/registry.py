from __future__ import annotations

from silas.models.skills import SkillDefinition


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


__all__ = ["SkillRegistry"]
