from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter

from silas.execution.sandbox import SandboxExecResult
from silas.models.execution import Sandbox, SandboxConfig

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE = "python:3.12-slim"


class DockerSandboxManager:
    """Docker-based sandbox backend for stronger isolation than subprocess.

    Uses the docker CLI directly (no docker-py) so the only hard dependency
    is a working ``docker`` binary on $PATH.  Falls back to raising clear
    errors when Docker is unavailable rather than silently degrading.
    """

    def __init__(
        self,
        *,
        base_image: str = _DEFAULT_IMAGE,
        docker_bin: str | None = None,
    ) -> None:
        self._base_image = base_image
        # Resolve once so we fail fast if docker is missing
        self._docker_bin = docker_bin or shutil.which("docker") or "docker"
        self._containers: dict[str, _ContainerInfo] = {}

    # --- SandboxManager protocol ------------------------------------------------

    async def create(self, config: SandboxConfig) -> Sandbox:
        if config.max_memory_mb <= 0:
            raise ValueError("max_memory_mb must be greater than zero")
        if config.max_cpu_seconds <= 0:
            raise ValueError("max_cpu_seconds must be greater than zero")

        sandbox_id = uuid.uuid4().hex
        container_name = f"silas-sandbox-{sandbox_id[:12]}"
        work_dir = str(Path(config.work_dir).resolve())

        # Build `docker create` once; the container sits idle until exec calls
        cmd = self._build_create_command(config, container_name, work_dir)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"docker create failed (rc={proc.returncode}): {detail}")

        container_id = stdout.decode().strip()

        # Start the container so exec works
        await self._run_docker("start", container_id)

        sandbox = Sandbox(
            sandbox_id=sandbox_id,
            config=config.model_copy(deep=True),
            work_dir=work_dir,
        )
        self._containers[sandbox_id] = _ContainerInfo(
            container_id=container_id,
            container_name=container_name,
        )
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
        info = self._containers.get(sandbox_id)
        if info is None:
            raise KeyError(f"unknown sandbox_id '{sandbox_id}'")

        exec_cmd: list[str] = [self._docker_bin, "exec"]

        if env:
            for key, val in env.items():
                exec_cmd.extend(["-e", f"{key}={val}"])

        exec_cmd.extend([info.container_id, *command])

        started = perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=float(timeout_seconds),
            )
        except TimeoutError:
            timed_out = True
            proc.kill()
            stdout_bytes, stderr_bytes = await proc.communicate()

        duration = perf_counter() - started
        return SandboxExecResult(
            exit_code=proc.returncode,
            stdout=_decode(stdout_bytes, max_output_bytes),
            stderr=_decode(stderr_bytes, max_output_bytes),
            timed_out=timed_out,
            duration_seconds=duration,
        )

    async def destroy(self, sandbox_id: str) -> None:
        info = self._containers.pop(sandbox_id, None)
        if info is None:
            return

        # Force-remove stops and deletes in one call
        await self._run_docker("rm", "-f", info.container_id)

    # --- internals --------------------------------------------------------------

    def _build_create_command(
        self,
        config: SandboxConfig,
        container_name: str,
        work_dir: str,
    ) -> list[str]:
        cmd: list[str] = [
            self._docker_bin,
            "create",
            "--name",
            container_name,
            # Security: read-only root prevents writes outside designated dirs
            "--read-only",
            # Writable /tmp so programs that need temp files still work
            "--tmpfs",
            "/tmp:rw,noexec,size=64m",  # noqa: S108
            # Mount the host working directory into the container
            "-v",
            f"{work_dir}:/workspace",
            "-w",
            "/workspace",
        ]

        # Resource limits — translate our config into docker flags
        cmd.extend(["--memory", f"{config.max_memory_mb}m"])
        cmd.extend(["--cpus", str(config.max_cpu_seconds)])

        # Network isolation is the default; only enable networking when asked
        if not config.network_access:
            cmd.extend(["--network", "none"])

        # Environment variables from config
        for key, val in config.env.items():
            cmd.extend(["-e", f"{key}={val}"])

        # Keep container alive with a blocking entrypoint so `docker exec` works
        cmd.extend([self._base_image, "sleep", "infinity"])
        return cmd

    async def _run_docker(self, *args: str) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            self._docker_bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"docker {args[0]} failed (rc={proc.returncode}): {detail}")
        return stdout


class _ContainerInfo:
    """Bookkeeping for a live container."""

    __slots__ = ("container_id", "container_name")

    def __init__(self, container_id: str, container_name: str) -> None:
        self.container_id = container_id
        self.container_name = container_name


def _decode(data: bytes, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace")


def is_docker_available(docker_bin: str | None = None) -> bool:
    """Quick probe: can we talk to the Docker daemon?"""
    bin_path = docker_bin or shutil.which("docker") or "docker"
    try:
        result = subprocess.run(
            [bin_path, "info", "--format", "{{.ID}}"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


__all__ = ["DockerSandboxManager", "is_docker_available"]
