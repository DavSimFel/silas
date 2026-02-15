from __future__ import annotations

from typing import Protocol, runtime_checkable

from silas.execution.docker_sandbox import DockerSandboxManager, is_docker_available
from silas.execution.python_exec import PythonExecutor
from silas.execution.sandbox import SandboxExecResult, SubprocessSandboxManager
from silas.execution.sandbox_factory import create_sandbox_manager
from silas.execution.shell import ShellExecutor
from silas.execution.worktree import LiveWorktreeManager
from silas.models.execution import ExecutionEnvelope, ExecutionResult


@runtime_checkable
class EphemeralExecutor(Protocol):
    async def execute(self, envelope: ExecutionEnvelope) -> ExecutionResult: ...


__all__ = [
    "DockerSandboxManager",
    "EphemeralExecutor",
    "LiveWorktreeManager",
    "PythonExecutor",
    "SandboxExecResult",
    "ShellExecutor",
    "SubprocessSandboxManager",
    "create_sandbox_manager",
    "is_docker_available",
]
