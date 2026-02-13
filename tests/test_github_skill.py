"""Tests for the GitHub skill (silas.skills.shipped.github_skill)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from silas.models.skills import SkillDefinition
from silas.skills.registry import SkillRegistry
from silas.skills.shipped.github_skill import (
    _run,
    commit_and_push,
    create_branch,
    create_pr,
    github_skill_definitions,
    read_file,
    read_issue,
    register_github_skills,
    write_file,
)

# ---------------------------------------------------------------------------
# _run helper
# ---------------------------------------------------------------------------


class TestRunHelper:
    def test_success(self) -> None:
        with patch("subprocess.run") as mock:
            mock.return_value = subprocess.CompletedProcess(
                args=["echo", "hi"], returncode=0, stdout="hi\n", stderr=""
            )
            result = _run(["echo", "hi"])
        assert result["returncode"] == 0
        assert result["stdout"] == "hi\n"

    def test_called_process_error(self) -> None:
        with patch("subprocess.run") as mock:
            mock.side_effect = subprocess.CalledProcessError(
                1, "git", output="", stderr="fatal"
            )
            result = _run(["git", "status"])
        assert "error" in result
        assert result["returncode"] == 1

    def test_timeout(self) -> None:
        with patch("subprocess.run") as mock:
            mock.side_effect = subprocess.TimeoutExpired("cmd", 120)
            result = _run(["sleep", "999"])
        assert "timed out" in str(result["error"])

    def test_file_not_found(self) -> None:
        with patch("subprocess.run") as mock:
            mock.side_effect = FileNotFoundError()
            result = _run(["nonexistent"])
        assert "command not found" in str(result["error"])


# ---------------------------------------------------------------------------
# read_issue
# ---------------------------------------------------------------------------


class TestReadIssue:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        issue_data = {
            "title": "Bug",
            "body": "desc",
            "labels": [],
            "comments": [],
            "state": "OPEN",
            "number": 42,
        }
        with patch("silas.skills.shipped.github_skill._run") as mock:
            mock.return_value = {
                "stdout": json.dumps(issue_data),
                "stderr": "",
                "returncode": 0,
            }
            result = await read_issue({"repo": "owner/repo", "issue_number": 42})
        assert result["issue"]["title"] == "Bug"

    @pytest.mark.asyncio
    async def test_error(self) -> None:
        with patch("silas.skills.shipped.github_skill._run") as mock:
            mock.return_value = {"error": "not found", "returncode": 1}
            result = await read_issue({"repo": "owner/repo", "issue_number": 999})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_repo(self) -> None:
        with pytest.raises(ValueError, match="repo"):
            await read_issue({"issue_number": 1})


# ---------------------------------------------------------------------------
# create_branch
# ---------------------------------------------------------------------------


class TestCreateBranch:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        with patch("silas.skills.shipped.github_skill._run") as mock:
            mock.return_value = {"stdout": "", "stderr": "", "returncode": 0}
            result = await create_branch(
                {"repo_path": "/tmp/repo", "branch_name": "feat/x"}
            )
        assert result["branch"] == "feat/x"
        assert result["base"] == "dev"
        assert mock.call_count == 2  # fetch + checkout

    @pytest.mark.asyncio
    async def test_fetch_error(self) -> None:
        with patch("silas.skills.shipped.github_skill._run") as mock:
            mock.return_value = {"error": "network", "returncode": 1}
            result = await create_branch(
                {"repo_path": "/tmp/repo", "branch_name": "feat/x"}
            )
        assert "error" in result


# ---------------------------------------------------------------------------
# read_file / write_file
# ---------------------------------------------------------------------------


class TestReadFile:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        (tmp_path / "hello.txt").write_text("world")
        result = await read_file(
            {"repo_path": str(tmp_path), "file_path": "hello.txt"}
        )
        assert result["content"] == "world"

    @pytest.mark.asyncio
    async def test_not_found(self, tmp_path: Path) -> None:
        result = await read_file(
            {"repo_path": str(tmp_path), "file_path": "nope.txt"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_path_traversal(self, tmp_path: Path) -> None:
        result = await read_file(
            {"repo_path": str(tmp_path), "file_path": "../../etc/passwd"}
        )
        assert "error" in result
        assert "traversal" in str(result["error"])


class TestWriteFile:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        result = await write_file(
            {
                "repo_path": str(tmp_path),
                "file_path": "sub/new.txt",
                "content": "hi",
            }
        )
        assert result["written"] == "sub/new.txt"
        assert (tmp_path / "sub" / "new.txt").read_text() == "hi"

    @pytest.mark.asyncio
    async def test_path_traversal(self, tmp_path: Path) -> None:
        result = await write_file(
            {
                "repo_path": str(tmp_path),
                "file_path": "../../evil.txt",
                "content": "bad",
            }
        )
        assert "traversal" in str(result["error"])


# ---------------------------------------------------------------------------
# commit_and_push
# ---------------------------------------------------------------------------


class TestCommitAndPush:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        with patch("silas.skills.shipped.github_skill._run") as mock:
            mock.return_value = {"stdout": "", "stderr": "", "returncode": 0}
            result = await commit_and_push(
                {"repo_path": "/tmp/repo", "message": "feat: x"}
            )
        assert result["committed"] is True
        assert mock.call_count == 3  # add, commit, push

    @pytest.mark.asyncio
    async def test_commit_error(self) -> None:
        call_count = 0

        def side_effect(cmd: list[str], *, cwd: str | None = None, check: bool = True) -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # commit step
                return {"error": "nothing to commit", "returncode": 1}
            return {"stdout": "", "stderr": "", "returncode": 0}

        with patch("silas.skills.shipped.github_skill._run", side_effect=side_effect):
            result = await commit_and_push(
                {"repo_path": "/tmp/repo", "message": "feat: x"}
            )
        assert "error" in result


# ---------------------------------------------------------------------------
# create_pr
# ---------------------------------------------------------------------------


class TestCreatePR:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        with patch("silas.skills.shipped.github_skill._run") as mock:
            mock.return_value = {
                "stdout": "https://github.com/owner/repo/pull/1\n",
                "stderr": "",
                "returncode": 0,
            }
            result = await create_pr(
                {
                    "repo": "owner/repo",
                    "title": "feat: x",
                    "body": "Closes #1",
                }
            )
        assert result["pr_url"] == "https://github.com/owner/repo/pull/1"

    @pytest.mark.asyncio
    async def test_with_head(self) -> None:
        with patch("silas.skills.shipped.github_skill._run") as mock:
            mock.return_value = {
                "stdout": "https://github.com/o/r/pull/2\n",
                "stderr": "",
                "returncode": 0,
            }
            await create_pr(
                {
                    "repo": "o/r",
                    "title": "t",
                    "body": "b",
                    "head": "feat/x",
                }
            )
        call_args = mock.call_args[0][0]
        assert "--head" in call_args
        assert "feat/x" in call_args

    @pytest.mark.asyncio
    async def test_missing_title(self) -> None:
        with pytest.raises(ValueError, match="title"):
            await create_pr({"repo": "o/r", "body": "b"})


# ---------------------------------------------------------------------------
# Definitions & registration
# ---------------------------------------------------------------------------


class TestDefinitions:
    def test_all_definitions_valid(self) -> None:
        defs = github_skill_definitions()
        assert len(defs) == 6
        for d in defs:
            assert isinstance(d, SkillDefinition)
            assert d.name.startswith("gh_")
            assert d.taint_level in ("owner", "external")

    def test_register(self) -> None:
        registry = SkillRegistry()
        register_github_skills(registry)
        assert registry.has("gh_read_issue")
        assert registry.has("gh_create_pr")
        assert len([s for s in registry.list_all() if s.name.startswith("gh_")]) == 6

    def test_register_with_executor(self) -> None:
        from silas.skills.executor import SkillExecutor

        registry = SkillRegistry()
        executor = SkillExecutor(registry)
        register_github_skills(registry, executor)
        assert registry.has("gh_commit_and_push")
        assert "gh_commit_and_push" in executor._handlers

    def test_high_taint_write_ops(self) -> None:
        """Write operations must be classified as owner (high) taint."""
        defs = {d.name: d for d in github_skill_definitions()}
        for name in ("gh_create_branch", "gh_write_file", "gh_commit_and_push", "gh_create_pr"):
            assert defs[name].taint_level == "owner", f"{name} should be owner taint"
            assert defs[name].requires_approval is True, f"{name} should require approval"
