from __future__ import annotations

import asyncio
import logging
import os
import platform
import resource
import shutil
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from silas.models.execution import Sandbox, SandboxConfig


@dataclass(slots=True)
class SandboxExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


logger = logging.getLogger(__name__)

_MINIMAL_PATH = "/usr/local/bin:/usr/bin:/bin"


class SubprocessSandboxManager:
    """Subprocess-based sandbox backend with per-run isolated working directories."""

    def __init__(
        self, base_dir: str | Path | None = None, preserve_sandboxes: bool = False
    ) -> None:
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None
        self._preserve_sandboxes = preserve_sandboxes
        self._sandboxes: dict[str, Sandbox] = {}
        self._active_pids: dict[str, set[int]] = {}  # sandbox_id → active process PIDs
        self._unshare_bin = shutil.which("unshare")
        self._network_isolation_checked = False

    async def create(self, config: SandboxConfig) -> Sandbox:
        if config.max_memory_mb <= 0:
            raise ValueError("max_memory_mb must be greater than zero")
        if config.max_cpu_seconds <= 0:
            raise ValueError("max_cpu_seconds must be greater than zero")
        if not config.network_access:
            self._ensure_network_isolation_capability()

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
        timeout_seconds: float = 60,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int = 100_000,
    ) -> SandboxExecResult:
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise KeyError(f"unknown sandbox_id '{sandbox_id}'")

        command_parts = self._normalize_command(command)
        command_parts = self._apply_network_policy(command_parts, sandbox.config)

        run_env = self._build_env(sandbox, env)
        started = perf_counter()
        self._active_pids.setdefault(sandbox_id, set())
        process = await asyncio.create_subprocess_exec(
            *command_parts,
            cwd=sandbox.work_dir,
            env=run_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            preexec_fn=self._build_preexec_fn(sandbox.config),
        )

        self._active_pids[sandbox_id].add(process.pid)
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
        except TimeoutError:
            timed_out = True
            self._terminate_process(process)
            stdout_bytes, stderr_bytes = await process.communicate()

        duration_seconds = perf_counter() - started
        pids = self._active_pids.get(sandbox_id)
        if pids is not None:
            pids.discard(process.pid)
        return SandboxExecResult(
            exit_code=process.returncode,
            stdout=self._decode_output(stdout_bytes, max_output_bytes),
            stderr=self._decode_output(stderr_bytes, max_output_bytes),
            timed_out=timed_out,
            duration_seconds=duration_seconds,
        )

    async def destroy(self, sandbox_id: str) -> None:
        # Kill any lingering processes before removing the sandbox
        lingering_pids = self._active_pids.pop(sandbox_id, set())
        for pid in lingering_pids:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:  # pragma: no cover - platform dependent
                logger.debug("Failed to kill process group %d during sandbox destroy", pid)

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
            "PATH": _MINIMAL_PATH,
            "HOME": sandbox.work_dir,
        }
        runtime_env.update({k: str(v) for k, v in sandbox.config.env.items()})
        if env:
            runtime_env.update({k: str(v) for k, v in env.items()})
        return runtime_env

    def _normalize_command(self, command: Sequence[str]) -> list[str]:
        if isinstance(command, str):
            raise ValueError("command must be an argument list, not a shell string")

        parts = [str(part) for part in command]
        if not parts:
            raise ValueError("command must not be empty")
        if not parts[0]:
            raise ValueError("command executable must not be empty")
        executable = Path(parts[0]).name
        if executable in {"bash", "sh", "zsh", "dash"} and len(parts) > 1 and parts[1] == "-c":
            raise ValueError("shell '-c' execution is not allowed; provide argument-list commands")
        return parts

    def _apply_network_policy(self, command: list[str], config: SandboxConfig) -> list[str]:
        if config.network_access:
            return command

        self._ensure_network_isolation_capability()
        if self._unshare_bin is None:
            raise RuntimeError("network isolation unavailable: missing 'unshare'")
        return [self._unshare_bin, "-n", "--", *command]

    def _ensure_network_isolation_capability(self) -> None:
        if self._network_isolation_checked:
            return

        if platform.system() != "Linux":
            raise RuntimeError("network isolation unavailable: requires Linux network namespaces")
        if self._unshare_bin is None:
            raise RuntimeError("network isolation unavailable: missing 'unshare' binary")

        probe = subprocess.run(
            [self._unshare_bin, "-n", "true"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            detail = probe.stderr.strip() or probe.stdout.strip() or f"exit code {probe.returncode}"
            raise RuntimeError(
                f"network isolation unavailable: cannot create network namespace ({detail})",
            )

        self._network_isolation_checked = True

    def _build_preexec_fn(self, config: SandboxConfig) -> Callable[[], None]:
        def _set_limits() -> None:
            memory_bytes = int(config.max_memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))

            cpu_seconds = max(1, int(config.max_cpu_seconds))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

        return _set_limits

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
        except OSError:  # pragma: no cover - platform dependent
            process.kill()


__all__ = ["SandboxExecResult", "SubprocessSandboxManager"]
