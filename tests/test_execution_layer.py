"""Tests for execution layer: sandbox manager, shell executor, python executor."""

from __future__ import annotations

from pathlib import Path

import pytest
from silas.execution.python import PythonExecutor
from silas.execution.sandbox import SubprocessSandboxManager
from silas.execution.shell import ShellExecutor
from silas.models.execution import ExecutionEnvelope, SandboxConfig

# --- SubprocessSandboxManager ---


class TestSubprocessSandboxManager:
    @pytest.mark.asyncio
    async def test_create_and_destroy(self, tmp_path: Path) -> None:
        mgr = SubprocessSandboxManager(base_dir=tmp_path)
        sandbox = await mgr.create(SandboxConfig(work_dir=str(tmp_path / "work")))
        assert sandbox.sandbox_id
        assert Path(sandbox.work_dir).exists()
        await mgr.destroy(sandbox.sandbox_id)
        assert not Path(sandbox.work_dir).exists()

    @pytest.mark.asyncio
    async def test_exec_simple_command(self, tmp_path: Path) -> None:
        mgr = SubprocessSandboxManager(base_dir=tmp_path)
        sandbox = await mgr.create(SandboxConfig(work_dir=str(tmp_path / "work")))
        result = await mgr.exec(sandbox.sandbox_id, ["echo", "hello"], timeout_seconds=5)
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert not result.timed_out
        await mgr.destroy(sandbox.sandbox_id)

    @pytest.mark.asyncio
    async def test_exec_timeout(self, tmp_path: Path) -> None:
        mgr = SubprocessSandboxManager(base_dir=tmp_path)
        sandbox = await mgr.create(SandboxConfig(work_dir=str(tmp_path / "work")))
        result = await mgr.exec(sandbox.sandbox_id, ["sleep", "10"], timeout_seconds=0.5)
        assert result.timed_out is True
        await mgr.destroy(sandbox.sandbox_id)

    @pytest.mark.asyncio
    async def test_exec_nonexistent_sandbox(self) -> None:
        mgr = SubprocessSandboxManager()
        with pytest.raises(KeyError, match="unknown sandbox_id"):
            await mgr.exec("nonexistent", ["echo", "hi"], timeout_seconds=5)

    @pytest.mark.asyncio
    async def test_exec_empty_command(self, tmp_path: Path) -> None:
        mgr = SubprocessSandboxManager(base_dir=tmp_path)
        sandbox = await mgr.create(SandboxConfig(work_dir=str(tmp_path / "work")))
        with pytest.raises(ValueError, match="must not be empty"):
            await mgr.exec(sandbox.sandbox_id, [], timeout_seconds=5)
        await mgr.destroy(sandbox.sandbox_id)

    @pytest.mark.asyncio
    async def test_exec_captures_stderr(self, tmp_path: Path) -> None:
        mgr = SubprocessSandboxManager(base_dir=tmp_path)
        sandbox = await mgr.create(SandboxConfig(work_dir=str(tmp_path / "work")))
        result = await mgr.exec(
            sandbox.sandbox_id, ["bash", "-c", "echo error >&2"], timeout_seconds=5,
        )
        assert "error" in result.stderr
        await mgr.destroy(sandbox.sandbox_id)

    @pytest.mark.asyncio
    async def test_exec_nonzero_exit(self, tmp_path: Path) -> None:
        mgr = SubprocessSandboxManager(base_dir=tmp_path)
        sandbox = await mgr.create(SandboxConfig(work_dir=str(tmp_path / "work")))
        result = await mgr.exec(sandbox.sandbox_id, ["false"], timeout_seconds=5)
        assert result.exit_code != 0
        await mgr.destroy(sandbox.sandbox_id)

    @pytest.mark.asyncio
    async def test_destroy_idempotent(self, tmp_path: Path) -> None:
        mgr = SubprocessSandboxManager(base_dir=tmp_path)
        sandbox = await mgr.create(SandboxConfig(work_dir=str(tmp_path / "work")))
        await mgr.destroy(sandbox.sandbox_id)
        await mgr.destroy(sandbox.sandbox_id)  # Should not raise

    @pytest.mark.asyncio
    async def test_output_truncation(self, tmp_path: Path) -> None:
        mgr = SubprocessSandboxManager(base_dir=tmp_path)
        sandbox = await mgr.create(SandboxConfig(work_dir=str(tmp_path / "work")))
        result = await mgr.exec(
            sandbox.sandbox_id,
            ["python3", "-c", "print('x' * 5000)"],
            timeout_seconds=5,
            max_output_bytes=100,
        )
        assert len(result.stdout) <= 100
        await mgr.destroy(sandbox.sandbox_id)


# --- ShellExecutor ---


def _shell_envelope(command: str, **kwargs) -> ExecutionEnvelope:
    defaults = {
        "execution_id": "test-exec",
        "step_index": 0,
        "task_description": "test",
        "action": "shell_exec",
        "args": {"command": command},
    }
    defaults.update(kwargs)
    return ExecutionEnvelope(**defaults)


class TestShellExecutor:
    @pytest.mark.asyncio
    async def test_execute_echo(self) -> None:
        mgr = SubprocessSandboxManager()
        executor = ShellExecutor(sandbox_manager=mgr)
        envelope = _shell_envelope("echo 'shell test'")
        result = await executor.execute(envelope)
        assert result.success is True
        assert "shell test" in result.return_value

    @pytest.mark.asyncio
    async def test_execute_failure(self) -> None:
        mgr = SubprocessSandboxManager()
        executor = ShellExecutor(sandbox_manager=mgr)
        envelope = _shell_envelope("exit 42")
        result = await executor.execute(envelope)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_wrong_action_rejected(self) -> None:
        mgr = SubprocessSandboxManager()
        executor = ShellExecutor(sandbox_manager=mgr)
        envelope = _shell_envelope("echo hi", action="python_exec")
        result = await executor.execute(envelope)
        assert result.success is False
        assert "unsupported" in (result.error or "").lower()


# --- PythonExecutor ---


def _python_envelope(code: str, **kwargs) -> ExecutionEnvelope:
    defaults = {
        "execution_id": "test-exec",
        "step_index": 0,
        "task_description": "test",
        "action": "python_exec",
        "args": {"script": code},
    }
    defaults.update(kwargs)
    return ExecutionEnvelope(**defaults)


class TestPythonExecutor:
    @pytest.mark.asyncio
    async def test_execute_simple(self) -> None:
        mgr = SubprocessSandboxManager()
        executor = PythonExecutor(sandbox_manager=mgr)
        envelope = _python_envelope("print(1 + 1)")
        result = await executor.execute(envelope)
        assert result.success is True
        assert "2" in result.return_value

    @pytest.mark.asyncio
    async def test_execute_error(self) -> None:
        mgr = SubprocessSandboxManager()
        executor = PythonExecutor(sandbox_manager=mgr)
        envelope = _python_envelope("raise ValueError('boom')")
        result = await executor.execute(envelope)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_wrong_action_rejected(self) -> None:
        mgr = SubprocessSandboxManager()
        executor = PythonExecutor(sandbox_manager=mgr)
        envelope = _python_envelope("print(1)", action="shell_exec")
        result = await executor.execute(envelope)
        assert result.success is False
        assert "unsupported" in (result.error or "").lower()
