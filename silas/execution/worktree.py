"""Git-worktree workspace isolation for parallel executors (§7.4).

Each executor gets an ephemeral worktree created from the canonical
workspace's HEAD.  On success the worktree's diff is three-way-merged
back; on conflict the work item is marked blocked.  Worktrees are
cleaned up after merge or dead-letter.

Path convention::

    .runtime/worktrees/{scope_id}/{task_id}/{attempt}
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_RUNTIME_DIR = ".runtime/worktrees"


class LiveWorktreeManager:
    """Subprocess-based ``git worktree`` lifecycle manager.

    Parameters
    ----------
    canonical_root:
        Absolute path to the canonical workspace (the git repo root).
    runtime_dir:
        Relative or absolute directory under which worktrees are created.
        Defaults to ``<canonical_root>/.runtime/worktrees``.
    """

    def __init__(
        self,
        canonical_root: str,
        *,
        runtime_dir: str | None = None,
    ) -> None:
        self._canonical_root = Path(canonical_root).resolve()
        if runtime_dir is not None:
            self._runtime_dir = Path(runtime_dir).resolve()
        else:
            self._runtime_dir = self._canonical_root / _DEFAULT_RUNTIME_DIR

        # Per-scope merge lock - only one merge at a time per scope
        self._scope_locks: dict[str, asyncio.Lock] = {}

    # ── public API ──────────────────────────────────────────────────

    async def create(
        self,
        scope_id: str,
        task_id: str,
        attempt: int,
    ) -> str:
        """Create an ephemeral worktree from HEAD and return its path."""
        worktree_path = self._worktree_path(scope_id, task_id, attempt)
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        baseline = await self._git_rev_parse("HEAD")

        await self._run_git(
            "worktree",
            "add",
            "--detach",
            str(worktree_path),
            baseline,
        )

        logger.info(
            "worktree_created scope=%s task=%s attempt=%d path=%s baseline=%s",
            scope_id,
            task_id,
            attempt,
            worktree_path,
            baseline[:12],
        )
        return str(worktree_path)

    async def merge_back(
        self,
        worktree_path: str,
    ) -> tuple[bool, str | None]:
        """Three-way-merge worktree changes back into the canonical workspace.

        Acquires a per-scope merge lock to serialise merges within a scope.
        Returns ``(True, None)`` on clean merge or no-op, or
        ``(False, detail)`` on conflict.
        """
        wt = Path(worktree_path).resolve()
        scope_id = self._scope_from_path(wt)
        lock = self._scope_locks.setdefault(scope_id, asyncio.Lock())

        async with lock:
            # Check if the worktree has any changes
            has_changes = await self._worktree_has_changes(wt)
            if not has_changes:
                logger.info("worktree_merge_noop path=%s (no changes)", wt)
                return True, None

            # Commit worktree changes so we have a tree to merge
            await self._run_git_in(wt, "add", "-A")
            await self._run_git_in(
                wt,
                "commit",
                "-m",
                f"worktree changes from {wt.name}",
                "--allow-empty",
            )

            worktree_commit = await self._git_rev_parse_in(wt, "HEAD")
            baseline = await self._git_merge_base(wt, worktree_commit)

            # Create a patch and apply to canonical workspace
            try:
                diff = await self._run_git_in(
                    wt,
                    "diff",
                    baseline,
                    worktree_commit,
                )
                if not diff.strip():
                    return True, None

                # Apply the patch in the canonical workspace
                await self._run_git(
                    "apply",
                    "--3way",
                    "--whitespace=nowarn",
                    input_data=diff,
                )
                logger.info(
                    "worktree_merged path=%s baseline=%s commit=%s",
                    wt,
                    baseline[:12],
                    worktree_commit[:12],
                )
                return True, None

            except _GitConflictError as exc:
                logger.warning(
                    "worktree_merge_conflict path=%s detail=%s",
                    wt,
                    exc.detail,
                )
                # Abort the failed apply
                await self._run_git("apply", "--abort", check=False)
                return False, exc.detail

    async def destroy(self, worktree_path: str) -> None:
        """Remove the worktree directory and prune git metadata."""
        wt = Path(worktree_path).resolve()
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
        await self._run_git("worktree", "prune", check=False)
        logger.info("worktree_destroyed path=%s", wt)

    # ── helpers ─────────────────────────────────────────────────────

    def _worktree_path(self, scope_id: str, task_id: str, attempt: int) -> Path:
        """Convention: .runtime/worktrees/{scope_id}/{task_id}/{attempt}."""
        return self._runtime_dir / scope_id / task_id / str(attempt)

    def _scope_from_path(self, worktree: Path) -> str:
        """Extract scope_id from the worktree path convention."""
        try:
            relative = worktree.relative_to(self._runtime_dir)
            return str(relative.parts[0])
        except (ValueError, IndexError):
            return "unknown"

    async def _worktree_has_changes(self, worktree: Path) -> bool:
        """Check whether the worktree has uncommitted changes."""
        output = await self._run_git_in(worktree, "status", "--porcelain")
        return bool(output.strip())

    async def _git_rev_parse(self, ref: str) -> str:
        """Resolve a ref to its SHA in the canonical workspace."""
        output = await self._run_git("rev-parse", ref)
        return output.strip()

    async def _git_rev_parse_in(self, cwd: Path, ref: str) -> str:
        """Resolve a ref to its SHA inside a specific worktree."""
        output = await self._run_git_in(cwd, "rev-parse", ref)
        return output.strip()

    async def _git_merge_base(self, cwd: Path, commit: str) -> str:
        """Find the merge base between the worktree commit and canonical HEAD."""
        canonical_head = await self._git_rev_parse("HEAD")
        output = await self._run_git_in(cwd, "merge-base", canonical_head, commit)
        return output.strip()

    async def _run_git(
        self,
        *args: str,
        check: bool = True,
        input_data: str | None = None,
    ) -> str:
        """Run a git command in the canonical workspace."""
        return await self._run_git_in(
            self._canonical_root,
            *args,
            check=check,
            input_data=input_data,
        )

    async def _run_git_in(
        self,
        cwd: Path,
        *args: str,
        check: bool = True,
        input_data: str | None = None,
    ) -> str:
        """Run a git command in a specific directory."""
        cmd = ["git", *args]
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
        }
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if input_data else asyncio.subprocess.DEVNULL,
            env=env,
        )
        stdin_bytes = input_data.encode() if input_data else None
        stdout, stderr = await proc.communicate(input=stdin_bytes)
        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace")

        if proc.returncode != 0 and check:
            if "conflict" in stderr_text.lower() or "conflict" in stdout_text.lower():
                raise _GitConflictError(stderr_text or stdout_text)
            msg = f"git {' '.join(args)} failed (rc={proc.returncode}): {stderr_text}"
            raise RuntimeError(msg)

        return stdout_text


class _GitConflictError(Exception):
    """Raised when a git operation hits a merge conflict."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


__all__ = ["LiveWorktreeManager"]
