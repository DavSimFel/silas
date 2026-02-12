from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Mapping, Sequence

from silas.models.execution import Sandbox, SandboxConfig


@dataclass(slots=True)
class SandboxExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


class SubprocessSandboxManager:
    """Subprocess-based sandbox backend with per-run isolated working directories."""

    def __init__(self, base_dir: str | Path | None = None, preserve_sandboxes: bool = False) -> None:
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None
        self._preserve_sandboxes = preserve_sandboxes
        self._sandboxes: dict[str, Sandbox] = {}

    async def create(self, config: SandboxConfig) -> Sandbox:
        if config.network_access:
            raise ValueError("network_access is not supported by SubprocessSandboxManager")

        root = self._resolve_root(config.work_dir)
        root.mkdir(parents=True, exist_ok=True)

        work_dir = Path(tempfile.mkdtemp(prefix="silas-sandbox-", dir=str(root))).resolve()
        sandbox = Sandbox(
            sandbox_id=uuid.uuid4().hex,
            config=config.model_copy(deep=True),
            work_dir=str(work_dir),
        )
        self._sandboxes[sandbox.sandbox_id] = sandbox
        return sandbox

    async def exec(
        self,
        sandbox_id: str,
        command: Sequence[str],
        *,
        timeout_seconds: int = 60,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int = 100_000,
    ) -> SandboxExecResult:
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise KeyError(f"unknown sandbox_id '{sandbox_id}'")

        if not command:
            raise ValueError("command must not be empty")

        run_env = self._build_env(sandbox, env)
        started = perf_counter()
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=sandbox.work_dir,
            env=run_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        timed_out = False
        stdout_bytes: bytes
        stderr_bytes: bytes
        try:
            timeout = float(timeout_seconds)
            if timeout <= 0:
                raise ValueError("timeout_seconds must be greater than zero")
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            self._terminate_process(process)
            stdout_bytes, stderr_bytes = await process.communicate()

        duration_seconds = perf_counter() - started
        return SandboxExecResult(
            exit_code=process.returncode,
            stdout=self._decode_output(stdout_bytes, max_output_bytes),
            stderr=self._decode_output(stderr_bytes, max_output_bytes),
            timed_out=timed_out,
            duration_seconds=duration_seconds,
        )

    async def destroy(self, sandbox_id: str) -> None:
        sandbox = self._sandboxes.pop(sandbox_id, None)
        if sandbox is None or self._preserve_sandboxes:
            return

        shutil.rmtree(sandbox.work_dir, ignore_errors=True)

    def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        return self._sandboxes.get(sandbox_id)

    def _resolve_root(self, work_dir: str) -> Path:
        if self._base_dir is not None:
            return self._base_dir

        root = Path(work_dir)
        if not root.is_absolute():
            root = Path.cwd() / root
        return root.resolve()

    def _build_env(self, sandbox: Sandbox, env: Mapping[str, str] | None) -> dict[str, str]:
        runtime_env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": sandbox.work_dir,
        }
        runtime_env.update({k: str(v) for k, v in sandbox.config.env.items()})
        if env:
            runtime_env.update({k: str(v) for k, v in env.items()})
        return runtime_env

    def _decode_output(self, output: bytes, max_output_bytes: int) -> str:
        if max_output_bytes <= 0:
            return ""
        if len(output) > max_output_bytes:
            output = output[:max_output_bytes]
        return output.decode("utf-8", errors="replace")

    def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except Exception:  # pragma: no cover - platform dependent
            process.kill()


__all__ = ["SubprocessSandboxManager", "SandboxExecResult"]
