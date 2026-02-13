"""Tests for skill hash-bound versioning (INV-06).

Verifies that SkillHasher produces deterministic, tamper-sensitive digests
and that the loader blocks modified skills.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from silas.skills.hasher import SkillHasher
from silas.skills.loader import SecurityError, SilasSkillLoader

SKILL_MD = """\
---
name: test-skill
description: A test skill for hashing verification purposes.
activation: manual
requires_approval: false
---

# Test Skill
"""


def _make_skill(tmp_path: Path, name: str = "test-skill", extra_py: str = "") -> Path:
    """Create a minimal skill directory with SKILL.md and an optional .py file."""
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    if extra_py:
        scripts = skill_dir / "scripts"
        scripts.mkdir()
        (scripts / "run.py").write_text(extra_py, encoding="utf-8")
    return skill_dir


class TestSkillHasher:
    def test_deterministic_hash(self, tmp_path: Path) -> None:
        """Same content must always produce the same hash."""
        skill_dir = _make_skill(tmp_path, extra_py="print('hello')")
        h1 = SkillHasher.compute_hash(skill_dir)
        h2 = SkillHasher.compute_hash(skill_dir)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex length

    def test_hash_changes_on_content_change(self, tmp_path: Path) -> None:
        """Modifying any file must change the hash."""
        skill_dir = _make_skill(tmp_path, extra_py="x = 1")
        original = SkillHasher.compute_hash(skill_dir)

        (skill_dir / "scripts" / "run.py").write_text("x = 2", encoding="utf-8")
        modified = SkillHasher.compute_hash(skill_dir)

        assert original != modified

    def test_hash_ignores_pycache(self, tmp_path: Path) -> None:
        """__pycache__ and .pyc files must not affect the hash."""
        skill_dir = _make_skill(tmp_path, extra_py="pass")
        baseline = SkillHasher.compute_hash(skill_dir)

        cache_dir = skill_dir / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "run.cpython-312.pyc").write_bytes(b"\x00\x01\x02")

        assert SkillHasher.compute_hash(skill_dir) == baseline

    def test_hash_changes_on_file_rename(self, tmp_path: Path) -> None:
        """Renaming a file changes the hash because relative paths are hashed."""
        skill_dir = _make_skill(tmp_path, extra_py="pass")
        original = SkillHasher.compute_hash(skill_dir)

        src = skill_dir / "scripts" / "run.py"
        dst = skill_dir / "scripts" / "main.py"
        src.rename(dst)

        assert SkillHasher.compute_hash(skill_dir) != original

    def test_not_a_directory_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "not-a-dir.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="not a directory"):
            SkillHasher.compute_hash(f)


class TestLoaderIntegrity:
    def test_new_skill_returns_hash(self, tmp_path: Path) -> None:
        """First verification of a new skill (no stored hash) should succeed."""
        _make_skill(tmp_path)
        loader = SilasSkillLoader(tmp_path / "skills")
        ok, h = loader.verify_integrity("test-skill", stored_hash=None)
        assert ok is True
        assert len(h) == 64

    def test_unchanged_skill_passes(self, tmp_path: Path) -> None:
        """An unmodified skill must pass verification."""
        _make_skill(tmp_path, extra_py="pass")
        loader = SilasSkillLoader(tmp_path / "skills")
        _, h = loader.verify_integrity("test-skill", stored_hash=None)
        ok, h2 = loader.verify_integrity("test-skill", stored_hash=h)
        assert ok is True
        assert h == h2

    def test_modified_skill_raises_security_error(self, tmp_path: Path) -> None:
        """Tampered skill files must raise SecurityError."""
        skill_dir = _make_skill(tmp_path, extra_py="safe = True")
        loader = SilasSkillLoader(tmp_path / "skills")
        _, original_hash = loader.verify_integrity("test-skill", stored_hash=None)

        # Tamper with the skill after hash was stored
        (skill_dir / "scripts" / "run.py").write_text("safe = False", encoding="utf-8")

        with pytest.raises(SecurityError, match="integrity check"):
            loader.verify_integrity("test-skill", stored_hash=original_hash)

    def test_load_verifies_hash_success(self, tmp_path: Path) -> None:
        """load() returns content when skill is unmodified."""
        _make_skill(tmp_path, extra_py="x = 1")
        loader = SilasSkillLoader(tmp_path / "skills")
        content, h = loader.load("test-skill")
        assert "test-skill" in content
        # Second load with stored hash succeeds
        content2, h2 = loader.load("test-skill", stored_hash=h)
        assert content2 == content
        assert h2 == h

    def test_load_raises_on_tamper(self, tmp_path: Path) -> None:
        """load() raises SecurityError when files are tampered after install."""
        skill_dir = _make_skill(tmp_path, extra_py="safe = True")
        loader = SilasSkillLoader(tmp_path / "skills")
        _content, h = loader.load("test-skill")

        # Tamper
        (skill_dir / "scripts" / "run.py").write_text("safe = False", encoding="utf-8")

        with pytest.raises(SecurityError, match="integrity check"):
            loader.load("test-skill", stored_hash=h)

    def test_hash_stored_in_install_result(self, tmp_path: Path) -> None:
        """SkillInstaller.install should include verified_hash in result."""
        source_dir = _make_skill(tmp_path / "source", extra_py="pass")
        install_dir = tmp_path / "installed"
        install_dir.mkdir()

        from silas.skills.installer import SkillInstaller

        loader = SilasSkillLoader(install_dir)
        installer = SkillInstaller(loader, install_dir)
        result = installer.install(str(source_dir))

        assert result["installed"] is True
        assert "verified_hash" in result
        assert len(str(result["verified_hash"])) == 64
