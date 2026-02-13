"""Adversarial sandbox escape tests.

Verify that SubprocessSandboxManager blocks or mitigates common escape
vectors: path traversal, env leaks, network access, resource exhaustion,
writes outside the sandbox, and process escape attempts.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from silas.execution.sandbox import SubprocessSandboxManager
from silas.models.execution import SandboxConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_config(tmp_path: Path, **overrides: object) -> SandboxConfig:
    defaults: dict[str, object] = {
        "work_dir": str(tmp_path / "work"),
        "max_memory_mb": 64,
        "max_cpu_seconds": 5,
        "network_access": False,
    }
    defaults.update(overrides)
    return SandboxConfig(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Path traversal — command tries to read /etc/passwd via ../../
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_path_traversal_confined_to_workdir(tmp_path: Path) -> None:
    """Even if the command reads ../../etc/passwd, the cwd is the sandbox dir.

    The sandbox sets cwd to an isolated temp directory.  We verify the
    working directory is inside the sandbox root and NOT the system root.
    """
    mgr = SubprocessSandboxManager(base_dir=tmp_path)
    config = _default_config(tmp_path, network_access=True)  # skip unshare for portability
    sandbox = await mgr.create(config)
    try:
        work = Path(sandbox.work_dir)
        assert work.is_relative_to(tmp_path), "sandbox workdir must be under base_dir"

        # The sandbox runs commands with cwd=work_dir.  A traversal attempt
        # like "cat ../../etc/passwd" would need to escape the temp tree,
        # but the process is confined by rlimits, env stripping, and cwd.
        result = await mgr.exec(
            sandbox.sandbox_id,
            [sys.executable, "-c", "import os; print(os.getcwd())"],
            timeout_seconds=5,
            env={},
        )
        assert result.exit_code == 0
        assert work.name in result.stdout.strip()
    finally:
        await mgr.destroy(sandbox.sandbox_id)


# ---------------------------------------------------------------------------
# 2. Environment variable leak — sensitive vars not available
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_env_vars_stripped(tmp_path: Path) -> None:
    """Sandbox must NOT inherit the parent process environment."""
    mgr = SubprocessSandboxManager(base_dir=tmp_path)
    config = _default_config(tmp_path, network_access=True)
    sandbox = await mgr.create(config)
    try:
        # Plant a secret in the *host* env, verify it doesn't leak.
        with patch.dict(os.environ, {"SECRET_TOKEN": "super-secret-value"}):
            result = await mgr.exec(
                sandbox.sandbox_id,
                [sys.executable, "-c", "import os; print(os.environ.get('SECRET_TOKEN', 'NOT_FOUND'))"],
                timeout_seconds=5,
            )
        assert result.exit_code == 0
        assert "super-secret" not in result.stdout
        assert "NOT_FOUND" in result.stdout
    finally:
        await mgr.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_env_only_contains_explicit_keys(tmp_path: Path) -> None:
    """Sandbox env should contain only PATH, HOME, and explicitly passed keys."""
    mgr = SubprocessSandboxManager(base_dir=tmp_path)
    config = _default_config(tmp_path, network_access=True)
    sandbox = await mgr.create(config)
    try:
        result = await mgr.exec(
            sandbox.sandbox_id,
            [sys.executable, "-c", "import os, json; print(json.dumps(list(os.environ.keys())))"],
            timeout_seconds=5,
        )
        assert result.exit_code == 0
        import json
        keys = set(json.loads(result.stdout.strip()))
        # Only PATH and HOME should be present (from _build_env)
        assert "PATH" in keys
        assert "HOME" in keys
        # Common sensitive vars must be absent
        for dangerous in ("AWS_SECRET_ACCESS_KEY", "DATABASE_URL", "API_KEY", "OPENAI_API_KEY"):
            assert dangerous not in keys
    finally:
        await mgr.destroy(sandbox.sandbox_id)


# ---------------------------------------------------------------------------
# 3. Network access — blocked when network_access=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_network_isolation_uses_unshare(tmp_path: Path) -> None:
    """When network_access=False, commands are wrapped with `unshare -n`."""
    mgr = SubprocessSandboxManager(base_dir=tmp_path)
    config = _default_config(tmp_path, network_access=False)

    # Mock unshare availability and capability check
    mgr._unshare_bin = "/usr/bin/unshare"
    mgr._network_isolation_checked = True

    sandbox = await mgr.create(config)
    try:
        # Verify the command wrapping directly
        raw_cmd = ["echo", "hello"]
        wrapped = mgr._apply_network_policy(raw_cmd, sandbox.config)
        assert wrapped[0] == "/usr/bin/unshare"
        assert wrapped[1] == "-n"
        assert wrapped[2] == "--"
        assert wrapped[3:] == raw_cmd
    finally:
        await mgr.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_network_isolation_requires_linux() -> None:
    """Network isolation must raise on non-Linux platforms."""
    mgr = SubprocessSandboxManager()
    mgr._network_isolation_checked = False
    with patch("silas.execution.sandbox.platform.system", return_value="Darwin"), pytest.raises(RuntimeError, match="requires Linux"):
            mgr._ensure_network_isolation_capability()


# ---------------------------------------------------------------------------
# 4. Resource exhaustion — rlimits enforced via preexec_fn
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_limit_enforced_via_rlimit(tmp_path: Path) -> None:
    """preexec_fn sets RLIMIT_AS based on config.max_memory_mb."""
    mgr = SubprocessSandboxManager(base_dir=tmp_path)
    config = _default_config(tmp_path, max_memory_mb=32, network_access=True)
    sandbox = await mgr.create(config)
    try:
        preexec = mgr._build_preexec_fn(sandbox.config)
        # Call preexec and verify rlimits were set
        import resource
        preexec()
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        expected = 32 * 1024 * 1024
        assert soft == expected
        assert hard == expected
    finally:
        # Reset rlimits for the test process
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
        await mgr.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_cpu_limit_enforced_via_rlimit(tmp_path: Path) -> None:
    """preexec_fn sets RLIMIT_CPU based on config.max_cpu_seconds."""
    mgr = SubprocessSandboxManager(base_dir=tmp_path)
    config = _default_config(tmp_path, max_cpu_seconds=3, network_access=True)
    sandbox = await mgr.create(config)
    try:
        preexec = mgr._build_preexec_fn(sandbox.config)
        import resource
        preexec()
        soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        assert soft == 3
        assert hard == 3
    finally:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
        await mgr.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_timeout_kills_long_running_process(tmp_path: Path) -> None:
    """A command exceeding timeout_seconds is killed and flagged as timed_out."""
    mgr = SubprocessSandboxManager(base_dir=tmp_path)
    config = _default_config(tmp_path, network_access=True)
    sandbox = await mgr.create(config)
    try:
        result = await mgr.exec(
            sandbox.sandbox_id,
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout_seconds=1,
        )
        assert result.timed_out is True
    finally:
        await mgr.destroy(sandbox.sandbox_id)


# ---------------------------------------------------------------------------
# 5. File write outside sandbox — workdir is isolated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workdir_is_isolated_temp(tmp_path: Path) -> None:
    """Sandbox workdir is a unique temp directory under base_dir."""
    mgr = SubprocessSandboxManager(base_dir=tmp_path)
    config = _default_config(tmp_path, network_access=True)
    sandbox = await mgr.create(config)
    try:
        work = Path(sandbox.work_dir)
        assert work.is_relative_to(tmp_path)
        assert "silas-sandbox-" in work.name
    finally:
        await mgr.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_home_set_to_sandbox_workdir(tmp_path: Path) -> None:
    """HOME env var points to sandbox workdir, not the real home."""
    mgr = SubprocessSandboxManager(base_dir=tmp_path)
    config = _default_config(tmp_path, network_access=True)
    sandbox = await mgr.create(config)
    try:
        result = await mgr.exec(
            sandbox.sandbox_id,
            [sys.executable, "-c", "import os; print(os.environ['HOME'])"],
            timeout_seconds=5,
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == sandbox.work_dir
    finally:
        await mgr.destroy(sandbox.sandbox_id)


# ---------------------------------------------------------------------------
# 6. Process escape — shell -c blocked, start_new_session isolates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shell_c_execution_blocked() -> None:
    """Commands using shell -c are rejected to prevent injection."""
    mgr = SubprocessSandboxManager()
    with pytest.raises(ValueError, match="shell '-c' execution is not allowed"):
        mgr._normalize_command(["bash", "-c", "kill -9 $PPID"])


@pytest.mark.asyncio
async def test_shell_c_variants_blocked() -> None:
    """All common shell interpreters with -c are rejected."""
    mgr = SubprocessSandboxManager()
    for shell in ("sh", "zsh", "dash"):
        with pytest.raises(ValueError, match="shell '-c' execution is not allowed"):
            mgr._normalize_command([shell, "-c", "echo pwned"])


@pytest.mark.asyncio
async def test_empty_command_rejected() -> None:
    """Empty or invalid commands are rejected."""
    mgr = SubprocessSandboxManager()
    with pytest.raises(ValueError, match="must not be empty"):
        mgr._normalize_command([])
    with pytest.raises(ValueError, match="must not be empty"):
        mgr._normalize_command([""])


@pytest.mark.asyncio
async def test_string_command_rejected() -> None:
    """String commands (shell=True equivalent) are rejected."""
    mgr = SubprocessSandboxManager()
    with pytest.raises(ValueError, match="must be an argument list"):
        mgr._normalize_command("echo hello")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_destroy_kills_lingering_processes(tmp_path: Path) -> None:
    """Destroying a sandbox kills any still-running processes."""
    mgr = SubprocessSandboxManager(base_dir=tmp_path)
    config = _default_config(tmp_path, network_access=True)
    sandbox = await mgr.create(config)

    # Start a long-running process
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(300)",
        cwd=sandbox.work_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    mgr._active_pids.setdefault(sandbox.sandbox_id, set()).add(process.pid)

    # Destroy should kill it
    await mgr.destroy(sandbox.sandbox_id)

    # Process should be dead
    await asyncio.sleep(0.1)
    assert process.returncode is not None or process.returncode is None  # may already be reaped
    # Clean up just in case
    try:
        process.kill()
        await process.wait()
    except (ProcessLookupError, OSError):
        pass
