from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from silas.skills.installer import SkillInstaller
from silas.skills.loader import SilasSkillLoader


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "Skill used for deterministic test coverage.",
    body: str = "# Skill\n\nInstructions.",
    frontmatter_overrides: dict[str, object] | None = None,
    scripts: dict[str, str] | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter: dict[str, object] = {"name": name, "description": description}
    if frontmatter_overrides:
        frontmatter.update(frontmatter_overrides)

    yaml_block = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    (skill_dir / "SKILL.md").write_text(f"---\n{yaml_block}\n---\n\n{body}\n", encoding="utf-8")

    for relative_path, content in (scripts or {}).items():
        target = skill_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return skill_dir


def test_scan_finds_skills_in_directory(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "coding")
    _write_skill(skills_dir, "ops")

    loader = SilasSkillLoader(skills_dir)

    assert [item.name for item in loader.scan()] == ["coding", "ops"]


def test_load_metadata_returns_correct_skill_metadata(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "coding", frontmatter_overrides={"activation": "auto"})

    loader = SilasSkillLoader(skills_dir)
    metadata = loader.load_metadata("coding")

    assert metadata.name == "coding"
    assert metadata.activation == "auto"


def test_load_metadata_raises_for_missing_skill(tmp_path: Path) -> None:
    loader = SilasSkillLoader(tmp_path / "skills")

    with pytest.raises(ValueError, match="skill not found"):
        loader.load_metadata("missing")


def test_load_full_returns_complete_skill_md_content(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "coding", body="# Coding Skill\n\nExecute coding tasks.")

    loader = SilasSkillLoader(skills_dir)
    full = loader.load_full("coding")

    assert full.startswith("---")
    assert "Coding Skill" in full


def test_resolve_script_returns_absolute_path(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = _write_skill(skills_dir, "coding", scripts={"scripts/run.py": "print('ok')\n"})

    loader = SilasSkillLoader(skills_dir)
    resolved = loader.resolve_script("coding", "scripts/run.py")

    assert Path(resolved).is_absolute()
    assert Path(resolved) == (skill_dir / "scripts/run.py")


def test_resolve_script_blocks_path_traversal(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "coding", scripts={"scripts/run.py": "print('ok')\n"})
    (tmp_path / "outside.py").write_text("print('outside')\n", encoding="utf-8")

    loader = SilasSkillLoader(skills_dir)

    with pytest.raises(ValueError, match="escapes"):
        loader.resolve_script("coding", "../outside.py")


def test_resolve_script_raises_for_missing_file(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "coding")
    loader = SilasSkillLoader(skills_dir)

    with pytest.raises(ValueError, match="script not found"):
        loader.resolve_script("coding", "scripts/missing.py")


def test_validate_returns_valid_for_good_skill(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir,
        "coding",
        frontmatter_overrides={
            "script_args": {"scripts/run.py": {"path": {"type": "string"}}},
        },
        scripts={"scripts/run.py": "print('ok')\n"},
    )

    loader = SilasSkillLoader(skills_dir)
    report = loader.validate("coding")

    assert report["valid"] is True
    assert report["errors"] == []


def test_validate_catches_missing_description(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "broken"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\nname: broken\n---\n\n# Broken\n", encoding="utf-8")

    loader = SilasSkillLoader(skills_dir)
    report = loader.validate("broken")

    assert report["valid"] is False
    assert any("description" in str(error).lower() for error in report["errors"])


def test_validate_catches_forbidden_patterns_eval(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "coding", scripts={"scripts/run.py": "value = eval('1 + 1')\n"})

    loader = SilasSkillLoader(skills_dir)
    report = loader.validate("coding")

    assert report["valid"] is False
    assert any("eval(" in str(error) for error in report["errors"])


def test_validate_catches_syntax_errors(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "coding", scripts={"scripts/run.py": "def broken(:\n    pass\n"})

    loader = SilasSkillLoader(skills_dir)
    report = loader.validate("coding")

    assert report["valid"] is False
    assert any("syntax error" in str(error) for error in report["errors"])


def test_import_external_handles_openai_function_format(tmp_path: Path) -> None:
    loader = SilasSkillLoader(tmp_path / "skills")
    source = json.dumps(
        {
            "type": "function",
            "function": {
                "name": "deploy_app",
                "description": "Deploy application releases safely.",
                "parameters": {
                    "type": "object",
                    "properties": {"environment": {"type": "string"}},
                },
            },
        }
    )

    report = loader.import_external(source)

    assert report["format"] == "openai"
    assert report["skill_name"] == "deploy-app"
    assert "name: deploy-app" in str(report["skill_md"])


def test_skill_installer_install_flow(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    destination = tmp_path / "installed"
    source_skill = _write_skill(
        source_root,
        "coding",
        scripts={"scripts/run.py": "print('ok')\n"},
    )

    loader = SilasSkillLoader(destination)
    installer = SkillInstaller(loader=loader, skills_dir=destination)
    report = installer.install(str(source_skill))

    assert report["installed"] is True
    assert (destination / "coding" / "SKILL.md").exists()
    assert [item.name for item in loader.scan()] == ["coding"]


def test_skill_installer_install_requires_approval(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    destination = tmp_path / "installed"
    source_skill = _write_skill(
        source_root,
        "skill-maker",
        frontmatter_overrides={"requires_approval": True},
    )

    loader = SilasSkillLoader(destination)
    installer = SkillInstaller(loader=loader, skills_dir=destination)
    report = installer.install(str(source_skill))

    assert report["installed"] is False
    assert report["approval_required"] is True
    assert not (destination / "skill-maker").exists()


def test_skill_installer_uninstall(tmp_path: Path) -> None:
    destination = tmp_path / "installed"
    _write_skill(destination, "coding")
    loader = SilasSkillLoader(destination)
    installer = SkillInstaller(loader=loader, skills_dir=destination)

    assert installer.uninstall("coding") is True
    assert installer.uninstall("coding") is False


def test_skill_installer_list_installed(tmp_path: Path) -> None:
    destination = tmp_path / "installed"
    _write_skill(destination, "coding")
    _write_skill(destination, "skill-maker")
    loader = SilasSkillLoader(destination)
    installer = SkillInstaller(loader=loader, skills_dir=destination)

    names = [item.name for item in installer.list_installed()]

    assert names == ["coding", "skill-maker"]


def test_frontmatter_parsing_edge_cases(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "edge"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\r\n"
        "name: edge\r\n"
        "description: Handles edge parsing scenarios well.\r\n"
        "activation: auto\r\n"
        "---\r\n"
        "\r\n"
        "# Edge\r\n",
        encoding="utf-8",
    )

    loader = SilasSkillLoader(skills_dir)
    metadata = loader.load_metadata("edge")

    assert metadata.name == "edge"
    assert metadata.activation == "auto"


def test_empty_skills_directory(tmp_path: Path) -> None:
    loader = SilasSkillLoader(tmp_path / "skills")
    assert loader.scan() == []


def test_skill_with_script_args(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir,
        "ops",
        frontmatter_overrides={
            "script_args": {
                "scripts/run.py": {"target": {"type": "string"}},
                "scripts/prepare.py": {"flag": {"type": "boolean"}},
            }
        },
        scripts={
            "scripts/run.py": "print('run')\n",
            "scripts/prepare.py": "print('prepare')\n",
        },
    )

    loader = SilasSkillLoader(skills_dir)
    metadata = loader.load_metadata("ops")
    report = loader.validate("ops")

    assert "scripts/run.py" in metadata.script_args
    assert report["valid"] is True


def test_validate_references_catches_missing_script_args_target(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir,
        "ops",
        frontmatter_overrides={"script_args": {"scripts/missing.py": {"x": {"type": "string"}}}},
        scripts={"scripts/run.py": "print('run')\n"},
    )

    loader = SilasSkillLoader(skills_dir)
    report = loader.validate("ops")

    assert report["valid"] is False
    assert any("does not exist" in str(error) for error in report["errors"])
