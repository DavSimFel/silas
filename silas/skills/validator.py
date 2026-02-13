from __future__ import annotations

from pathlib import Path

from silas.models.skills import SkillMetadata

_FORBIDDEN_PATTERNS: tuple[str, ...] = ("eval(", "exec(", "__import__")


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


class SkillValidator:
    @staticmethod
    def validate_frontmatter(metadata: SkillMetadata) -> list[str]:
        return validate_frontmatter(metadata)

    @staticmethod
    def validate_scripts(skill_dir: Path) -> list[str]:
        return validate_scripts(skill_dir)

    @staticmethod
    def check_forbidden_patterns(skill_dir: Path) -> list[str]:
        return check_forbidden_patterns(skill_dir)

    @staticmethod
    def validate_references(skill_dir: Path, metadata: SkillMetadata) -> list[str]:
        return validate_references(skill_dir, metadata)


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


__all__ = [
    "SkillValidator",
    "check_forbidden_patterns",
    "validate_frontmatter",
    "validate_references",
    "validate_scripts",
]
