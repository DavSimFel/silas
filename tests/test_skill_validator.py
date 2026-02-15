from __future__ import annotations

from pathlib import Path

from silas.models.skills import SkillMetadata
from silas.skills.registry import (
    check_forbidden_patterns,
    validate_frontmatter,
    validate_references,
    validate_scripts,
)


def _metadata(
    name: str = "test-skill",
    description: str = "A valid test skill description",
    script_args: dict | None = None,
) -> SkillMetadata:
    return SkillMetadata(
        name=name,
        description=description,
        script_args=script_args or {},
    )


class TestValidateFrontmatter:
    def test_valid_metadata(self) -> None:
        errors = validate_frontmatter(_metadata())
        assert errors == []

    def test_empty_name(self) -> None:
        errors = validate_frontmatter(_metadata(name=""))
        assert any("name is required" in e for e in errors)

    def test_whitespace_name(self) -> None:
        errors = validate_frontmatter(_metadata(name="   "))
        assert any("name is required" in e for e in errors)

    def test_empty_description(self) -> None:
        errors = validate_frontmatter(_metadata(description=""))
        assert any("description is required" in e for e in errors)

    def test_short_description(self) -> None:
        errors = validate_frontmatter(_metadata(description="short"))
        assert any("between 10 and 500" in e for e in errors)

    def test_long_description(self) -> None:
        errors = validate_frontmatter(_metadata(description="x" * 501))
        assert any("between 10 and 500" in e for e in errors)

    def test_boundary_description_10_chars(self) -> None:
        errors = validate_frontmatter(_metadata(description="a" * 10))
        assert errors == []

    def test_boundary_description_500_chars(self) -> None:
        errors = validate_frontmatter(_metadata(description="a" * 500))
        assert errors == []

    def test_multiple_errors(self) -> None:
        errors = validate_frontmatter(_metadata(name="", description=""))
        assert len(errors) == 2


class TestValidateScripts:
    def test_valid_python_file(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1\n")
        errors = validate_scripts(tmp_path)
        assert errors == []

    def test_syntax_error(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("def f(\n")
        errors = validate_scripts(tmp_path)
        assert len(errors) == 1
        assert "syntax error" in errors[0]

    def test_empty_dir(self, tmp_path: Path) -> None:
        errors = validate_scripts(tmp_path)
        assert errors == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        errors = validate_scripts(tmp_path / "nonexistent")
        assert errors == []

    def test_nested_python_files(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        errors = validate_scripts(tmp_path)
        assert errors == []

    def test_non_python_files_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "data.txt").write_text("not python {{{\n")
        errors = validate_scripts(tmp_path)
        assert errors == []


class TestCheckForbiddenPatterns:
    def test_clean_code(self, tmp_path: Path) -> None:
        (tmp_path / "clean.py").write_text("x = 1 + 2\n")
        errors = check_forbidden_patterns(tmp_path)
        assert errors == []

    def test_eval_detected(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("result = eval('1+1')\n")
        errors = check_forbidden_patterns(tmp_path)
        assert len(errors) == 1
        assert "eval(" in errors[0]

    def test_exec_detected(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("exec('import os')\n")
        errors = check_forbidden_patterns(tmp_path)
        assert len(errors) == 1
        assert "exec(" in errors[0]

    def test_dunder_import_detected(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("mod = __import__('os')\n")
        errors = check_forbidden_patterns(tmp_path)
        assert len(errors) == 1
        assert "__import__" in errors[0]

    def test_multiple_violations(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("eval('x')\nexec('y')\n")
        errors = check_forbidden_patterns(tmp_path)
        assert len(errors) == 2

    def test_line_numbers_reported(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("ok = 1\nresult = eval('2')\n")
        errors = check_forbidden_patterns(tmp_path)
        assert ":2" in errors[0]


class TestValidateReferences:
    def test_valid_reference(self, tmp_path: Path) -> None:
        (tmp_path / "run.py").write_text("x = 1\n")
        meta = _metadata(script_args={"run.py": {}})
        errors = validate_references(tmp_path, meta)
        assert errors == []

    def test_missing_reference(self, tmp_path: Path) -> None:
        meta = _metadata(script_args={"missing.py": {}})
        errors = validate_references(tmp_path, meta)
        assert len(errors) == 1
        assert "does not exist" in errors[0]

    def test_directory_traversal_blocked(self, tmp_path: Path) -> None:
        meta = _metadata(script_args={"../../etc/passwd": {}})
        errors = validate_references(tmp_path, meta)
        assert len(errors) == 1
        assert "escapes skill directory" in errors[0]

    def test_multiple_references(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        meta = _metadata(script_args={"a.py": {}, "b.py": {}})
        errors = validate_references(tmp_path, meta)
        assert errors == []

    def test_reference_to_directory_fails(self, tmp_path: Path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        meta = _metadata(script_args={"subdir": {}})
        errors = validate_references(tmp_path, meta)
        assert len(errors) == 1
        assert "does not exist" in errors[0]
