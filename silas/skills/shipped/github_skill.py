"""GitHub skill — exposes repo operations via the ``gh`` CLI.

Every tool function shells out to ``gh`` / ``git`` so we piggy-back on
whatever authentication the host already has (``gh auth login``).
All functions are async-friendly but block on subprocess calls wrapped
with :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from silas.models.skills import SkillDefinition

# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run(
    cmd: list[str],
    *,
    cwd: str | None = None,
    check: bool = True,
) -> dict[str, object]:
    """Run *cmd* and return structured result or error."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            check=check,
            timeout=120,
        )
        return {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}
    except subprocess.CalledProcessError as exc:
        return {
            "error": f"command failed with exit code {exc.returncode}",
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "returncode": exc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "command timed out after 120 seconds", "returncode": -1}
    except FileNotFoundError:
        return {"error": f"command not found: {cmd[0]}", "returncode": -1}


async def _arun(
    cmd: list[str],
    *,
    cwd: str | None = None,
    check: bool = True,
) -> dict[str, object]:
    return await asyncio.to_thread(_run, cmd, cwd=cwd, check=check)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def read_issue(inputs: dict[str, object]) -> dict[str, object]:
    """Fetch issue details (title, body, labels, comments)."""
    repo = _req_str(inputs, "repo")
    issue_number = _req_int(inputs, "issue_number")

    result = await _arun(
        [
            "gh", "issue", "view", str(issue_number),
            "--repo", repo,
            "--json", "title,body,labels,comments,state,number",
        ],
    )
    if "error" in result:
        return result

    data = json.loads(str(result["stdout"]))
    return {"issue": data}


async def create_branch(inputs: dict[str, object]) -> dict[str, object]:
    """Create a feature branch from *base* (default ``dev``)."""
    repo_path = _req_str(inputs, "repo_path")
    branch_name = _req_str(inputs, "branch_name")
    base = str(inputs.get("base", "dev"))

    # Fetch and create branch
    fetch = await _arun(["git", "fetch", "origin", base], cwd=repo_path)
    if "error" in fetch:
        return fetch

    result = await _arun(
        ["git", "checkout", "-b", branch_name, f"origin/{base}"],
        cwd=repo_path,
    )
    if "error" in result:
        return result

    return {"branch": branch_name, "base": base}


async def read_file(inputs: dict[str, object]) -> dict[str, object]:
    """Read a file from the repo working tree."""
    repo_path = _req_str(inputs, "repo_path")
    file_path = _req_str(inputs, "file_path")

    target = Path(repo_path) / file_path
    resolved = target.resolve()
    repo_resolved = Path(repo_path).resolve()
    if not str(resolved).startswith(str(repo_resolved)):
        return {"error": "path traversal detected"}

    try:
        content = target.read_text()
    except FileNotFoundError:
        return {"error": f"file not found: {file_path}"}
    except OSError as exc:
        return {"error": str(exc)}

    return {"content": content, "path": file_path}


async def write_file(inputs: dict[str, object]) -> dict[str, object]:
    """Write *content* to a file in the repo working tree."""
    repo_path = _req_str(inputs, "repo_path")
    file_path = _req_str(inputs, "file_path")
    content = _req_str(inputs, "content")

    target = Path(repo_path) / file_path
    resolved = target.resolve()
    repo_resolved = Path(repo_path).resolve()
    if not str(resolved).startswith(str(repo_resolved)):
        return {"error": "path traversal detected"}

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return {"written": file_path, "bytes": len(content.encode())}


async def commit_and_push(inputs: dict[str, object]) -> dict[str, object]:
    """Stage all changes, commit, and push to origin."""
    repo_path = _req_str(inputs, "repo_path")
    message = _req_str(inputs, "message")
    branch = str(inputs.get("branch", ""))

    add = await _arun(["git", "add", "-A"], cwd=repo_path)
    if "error" in add:
        return add

    commit = await _arun(["git", "commit", "-m", message], cwd=repo_path)
    if "error" in commit:
        return commit

    push_cmd = ["git", "push", "origin"]
    if branch:
        push_cmd.append(branch)
    push = await _arun(push_cmd, cwd=repo_path)
    if "error" in push:
        return push

    return {"committed": True, "message": message}


async def create_pr(inputs: dict[str, object]) -> dict[str, object]:
    """Open a pull request via ``gh pr create``."""
    repo = _req_str(inputs, "repo")
    title = _req_str(inputs, "title")
    body = _req_str(inputs, "body")
    base = str(inputs.get("base", "dev"))
    head = str(inputs.get("head", ""))

    cmd = [
        "gh", "pr", "create",
        "--repo", repo,
        "--base", base,
        "--title", title,
        "--body", body,
    ]
    if head:
        cmd += ["--head", head]

    result = await _arun(cmd)
    if "error" in result:
        return result

    url = str(result.get("stdout", "")).strip()
    return {"pr_url": url}


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def _req_str(inputs: dict[str, object], key: str) -> str:
    val = inputs.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    return val


def _req_int(inputs: dict[str, object], key: str) -> int:
    val = inputs.get(key)
    if isinstance(val, int):
        return val
    if isinstance(val, str) and val.isdigit():
        return int(val)
    raise ValueError(f"'{key}' must be an integer")


# ---------------------------------------------------------------------------
# Skill definitions & registration
# ---------------------------------------------------------------------------

_TOOLS: dict[str, object] = {
    "gh_read_issue": read_issue,
    "gh_create_branch": create_branch,
    "gh_read_file": read_file,
    "gh_write_file": write_file,
    "gh_commit_and_push": commit_and_push,
    "gh_create_pr": create_pr,
}


def github_skill_definitions() -> list[SkillDefinition]:
    """Return :class:`SkillDefinition` objects for every GitHub tool."""
    return [
        SkillDefinition(
            name="gh_read_issue",
            description="Fetch GitHub issue details (title, body, labels, comments) via gh CLI.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/repo"},
                    "issue_number": {"type": "integer"},
                },
                "required": ["repo", "issue_number"],
            },
            output_schema={"type": "object"},
            requires_approval=False,
            max_retries=1,
            timeout_seconds=30,
            taint_level="external",
        ),
        SkillDefinition(
            name="gh_create_branch",
            description="Create a git feature branch from a base branch.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "branch_name": {"type": "string"},
                    "base": {"type": "string", "default": "dev"},
                },
                "required": ["repo_path", "branch_name"],
            },
            output_schema={"type": "object"},
            requires_approval=True,
            max_retries=0,
            timeout_seconds=60,
            taint_level="owner",
        ),
        SkillDefinition(
            name="gh_read_file",
            description="Read a file from a local repo working tree.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "file_path": {"type": "string"},
                },
                "required": ["repo_path", "file_path"],
            },
            output_schema={"type": "object"},
            requires_approval=False,
            max_retries=0,
            timeout_seconds=10,
            taint_level="external",
        ),
        SkillDefinition(
            name="gh_write_file",
            description="Write content to a file in a local repo working tree.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["repo_path", "file_path", "content"],
            },
            output_schema={"type": "object"},
            requires_approval=True,
            max_retries=0,
            timeout_seconds=10,
            taint_level="owner",
        ),
        SkillDefinition(
            name="gh_commit_and_push",
            description="Stage all changes, commit with a message, and push to origin.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "message": {"type": "string"},
                    "branch": {"type": "string"},
                },
                "required": ["repo_path", "message"],
            },
            output_schema={"type": "object"},
            requires_approval=True,
            max_retries=0,
            timeout_seconds=60,
            taint_level="owner",
        ),
        SkillDefinition(
            name="gh_create_pr",
            description="Open a GitHub pull request via gh CLI.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "base": {"type": "string", "default": "dev"},
                    "head": {"type": "string"},
                },
                "required": ["repo", "title", "body"],
            },
            output_schema={"type": "object"},
            requires_approval=True,
            max_retries=1,
            timeout_seconds=30,
            taint_level="owner",
        ),
    ]


def register_github_skills(
    skill_registry: object,
    executor: object | None = None,
) -> None:
    """Register all GitHub skills in *skill_registry*.

    If *executor* is provided (a :class:`SkillExecutor`), handlers are
    also wired up so the executor can run the tools.
    """
    from silas.skills.registry import SkillRegistry

    assert isinstance(skill_registry, SkillRegistry)

    for defn in github_skill_definitions():
        skill_registry.register(defn)

    if executor is not None:
        from silas.skills.executor import SkillExecutor

        assert isinstance(executor, SkillExecutor)
        for name, handler in _TOOLS.items():
            executor.register_handler(name, handler)  # type: ignore[arg-type]


__all__ = [
    "commit_and_push",
    "create_branch",
    "create_pr",
    "github_skill_definitions",
    "read_file",
    "read_issue",
    "register_github_skills",
    "write_file",
]
