"""Tests for the skill import/adaptation system (§10.4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from silas.models.skills import SkillDefinition
from silas.skills.hasher import SkillHasher
from silas.skills.importer import (
    DependencyError,
    SkillImporter,
    SkillImportError,
    SkillManifest,
)


@pytest.fixture
def importer() -> SkillImporter:
    return SkillImporter()


@pytest.fixture
def valid_skill_dir(tmp_path: Path) -> Path:
    """Create a minimal valid skill package directory."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    manifest = {
        "name": "my-skill",
        "version": "1.0.0",
        "description": "A test skill for validation",
        "author": "test",
        "tools": [{"name": "do_thing", "description": "does a thing"}],
        "taint_level": "external",
        "dependencies": [],
        "entry_point": "main.py",
    }
    (skill_dir / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (skill_dir / "main.py").write_text("# entry point\n", encoding="utf-8")
    return skill_dir


class TestImportSkill:
    def test_import_valid_manifest(
        self, importer: SkillImporter, valid_skill_dir: Path
    ) -> None:
        """A well-formed skill package should produce a SkillDefinition."""
        skill = importer.import_skill(valid_skill_dir, adapt=False)

        assert isinstance(skill, SkillDefinition)
        assert skill.name == "my-skill"
        assert skill.version == "1.0.0"
        assert skill.description == "A test skill for validation"
        assert skill.taint_level == "external"

    def test_import_json_manifest(
        self, importer: SkillImporter, tmp_path: Path
    ) -> None:
        """skill.json should work as an alternative to skill.yaml."""
        import json

        skill_dir = tmp_path / "json-skill"
        skill_dir.mkdir()
        manifest = {
            "name": "json-skill",
            "version": "0.1.0",
            "description": "Skill with JSON manifest",
        }
        (skill_dir / "skill.json").write_text(json.dumps(manifest), encoding="utf-8")

        skill = importer.import_skill(skill_dir, adapt=False)
        assert skill.name == "json-skill"

    def test_import_missing_manifest_raises(
        self, importer: SkillImporter, tmp_path: Path
    ) -> None:
        """Directory without a manifest file should raise SkillImportError."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with pytest.raises(SkillImportError, match="no manifest found"):
            importer.import_skill(empty_dir)

    def test_import_not_a_directory_raises(
        self, importer: SkillImporter, tmp_path: Path
    ) -> None:
        with pytest.raises(SkillImportError, match="not a directory"):
            importer.import_skill(tmp_path / "nonexistent")

    def test_import_invalid_manifest_raises(
        self, importer: SkillImporter, tmp_path: Path
    ) -> None:
        """Manifest missing required fields should fail validation."""
        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        # Missing 'name', 'version', 'description'
        (skill_dir / "skill.yaml").write_text("tools: []\n", encoding="utf-8")

        with pytest.raises(SkillImportError, match="invalid skill manifest"):
            importer.import_skill(skill_dir)

    def test_import_invalid_taint_level_raises(
        self, importer: SkillImporter, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "bad-taint"
        skill_dir.mkdir()
        manifest = {
            "name": "bad-taint",
            "version": "1.0.0",
            "description": "Skill with bad taint",
            "taint_level": "nuclear",
        }
        (skill_dir / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

        with pytest.raises(SkillImportError, match="invalid skill manifest"):
            importer.import_skill(skill_dir)


class TestHashComputation:
    def test_hash_matches_skill_hasher(
        self, importer: SkillImporter, valid_skill_dir: Path
    ) -> None:
        """Import hash must match a direct SkillHasher.compute_hash call."""
        skill = importer.import_skill(valid_skill_dir, adapt=False)
        expected_hash = SkillHasher.compute_hash(valid_skill_dir)
        assert skill.verified_hash == expected_hash

    def test_hash_changes_on_file_modification(
        self, importer: SkillImporter, valid_skill_dir: Path
    ) -> None:
        """Modifying a file should change the computed hash."""
        skill_before = importer.import_skill(valid_skill_dir, adapt=False)
        (valid_skill_dir / "main.py").write_text("# modified\n", encoding="utf-8")
        skill_after = importer.import_skill(valid_skill_dir, adapt=False)
        assert skill_before.verified_hash != skill_after.verified_hash


class TestAdaptation:
    def test_env_var_replacement(
        self, importer: SkillImporter, tmp_path: Path
    ) -> None:
        """${ENV_VAR} in description should be replaced during adaptation."""
        skill_dir = tmp_path / "env-skill"
        skill_dir.mkdir()
        manifest = {
            "name": "env-skill",
            "version": "1.0.0",
            "description": "Connects to ${API_HOST} on port ${API_PORT}",
            "dependencies": [],
        }
        (skill_dir / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

        skill = importer.import_skill(
            skill_dir,
            adapt=True,
            env={"API_HOST": "localhost", "API_PORT": "8080"},
        )
        assert skill.description == "Connects to localhost on port 8080"

    def test_unresolved_env_var_kept(
        self, importer: SkillImporter, tmp_path: Path
    ) -> None:
        """Unmatched ${VAR} placeholders should be left as-is."""
        skill_dir = tmp_path / "partial-env"
        skill_dir.mkdir()
        manifest = {
            "name": "partial-env",
            "version": "1.0.0",
            "description": "Host is ${KNOWN}, secret is ${UNKNOWN}",
            "dependencies": [],
        }
        (skill_dir / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

        skill = importer.import_skill(skill_dir, env={"KNOWN": "resolved"})
        assert "resolved" in skill.description
        assert "${UNKNOWN}" in skill.description

    def test_missing_dependency_raises(
        self, importer: SkillImporter, tmp_path: Path
    ) -> None:
        """Dependencies not on PATH should raise DependencyError."""
        skill_dir = tmp_path / "dep-skill"
        skill_dir.mkdir()
        manifest = {
            "name": "dep-skill",
            "version": "1.0.0",
            "description": "Needs a fake tool",
            "dependencies": ["definitely_not_a_real_binary_xyz123"],
        }
        (skill_dir / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

        with pytest.raises(DependencyError, match="missing dependencies"):
            importer.import_skill(skill_dir, adapt=True)

    def test_available_dependency_ok(
        self, importer: SkillImporter, tmp_path: Path
    ) -> None:
        """Dependencies that exist on PATH should pass validation."""
        skill_dir = tmp_path / "ok-dep"
        skill_dir.mkdir()
        manifest = {
            "name": "ok-dep",
            "version": "1.0.0",
            "description": "Uses python",
            # 'python3' should be available in any test environment
            "dependencies": ["python3"],
        }
        (skill_dir / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

        skill = importer.import_skill(skill_dir, adapt=True)
        assert skill.name == "ok-dep"


class TestTaintPropagation:
    def test_taint_level_from_manifest(
        self, importer: SkillImporter, valid_skill_dir: Path
    ) -> None:
        """Taint level declared in manifest should appear on SkillDefinition."""
        skill = importer.import_skill(valid_skill_dir, adapt=False)
        assert skill.taint_level == "external"

    def test_no_taint_level_defaults_none(
        self, importer: SkillImporter, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "no-taint"
        skill_dir.mkdir()
        manifest = {
            "name": "no-taint",
            "version": "1.0.0",
            "description": "No taint declared",
        }
        (skill_dir / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

        skill = importer.import_skill(skill_dir, adapt=False)
        assert skill.taint_level is None


class TestSkillManifestModel:
    def test_valid_manifest(self) -> None:
        m = SkillManifest(
            name="test", version="1.0.0", description="A test skill"
        )
        assert m.entry_point == "main.py"
        assert m.dependencies == []

    def test_invalid_taint_level(self) -> None:
        with pytest.raises(ValueError, match="taint_level"):
            SkillManifest(
                name="x", version="1.0.0", description="d", taint_level="bad"
            )
