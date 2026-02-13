"""Deterministic content hashing for skill integrity verification.

Skills are loaded from disk and executed with agent privileges. Without hash
tracking, an attacker (or accidental edit) can modify installed skill files
and the loader would happily activate the tampered code. This module produces
a deterministic SHA-256 digest of all meaningful skill files so we can detect
any post-install modification.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# Directories whose contents are build artifacts or VCS metadata,
# not authored skill code — excluding them keeps the hash stable
# across environments.
_EXCLUDED_DIRS: frozenset[str] = frozenset({"__pycache__", ".git"})

# Only these extensions/filenames carry meaningful skill logic or config.
_INCLUDED_EXTENSIONS: frozenset[str] = frozenset({".py", ".md", ".yaml", ".yml", ".toml", ".json"})


class SkillHasher:
    """Computes a deterministic content hash over a skill directory."""

    @staticmethod
    def compute_hash(skill_path: Path) -> str:
        """Return hex SHA-256 of all relevant files under *skill_path*.

        Files are sorted by their path relative to *skill_path* so the
        hash is independent of filesystem enumeration order.
        """
        resolved = skill_path.resolve()
        if not resolved.is_dir():
            raise ValueError(f"skill path is not a directory: {skill_path}")

        hasher = hashlib.sha256()
        files = sorted(_iter_hashable_files(resolved), key=lambda p: str(p.relative_to(resolved)))

        for file_path in files:
            # Include the relative path in the digest so renaming a file
            # changes the hash even if contents stay the same.
            rel = str(file_path.relative_to(resolved))
            hasher.update(rel.encode("utf-8"))
            hasher.update(file_path.read_bytes())

        return hasher.hexdigest()


def _iter_hashable_files(root: Path) -> list[Path]:
    """Collect files that contribute to the skill's identity."""
    result: list[Path] = []
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        # Skip anything inside excluded directory trees
        if any(part in _EXCLUDED_DIRS for part in item.relative_to(root).parts):
            continue
        # Skip compiled bytecode regardless of location
        if item.suffix == ".pyc":
            continue
        if item.suffix in _INCLUDED_EXTENSIONS:
            result.append(item)
    return result


__all__ = ["SkillHasher"]
