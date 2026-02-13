"""Tests for the Docker sandbox backend.

All tests mock the docker CLI — no real Docker daemon required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from silas.execution.docker_sandbox import DockerSandboxManager, is_docker_available
from silas.execution.sandbox import SubprocessSandboxManager
from silas.execution.sandbox_factory import create_sandbox_manager
from silas.models.execution import SandboxConfig


def _make_config(**overrides: object) -> SandboxConfig:
    defaults: dict[str, object] = {
        "work_dir": "/tmp/test-workdir",
        "max_memory_mb": 256,
        "max_cpu_seconds": 30,
    }
    defaults.update(overrides)
    return SandboxConfig(**defaults)  # type: ignore[arg-type]


def _fake_process(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> AsyncMock:
    """Build a mock that quacks like asyncio.subprocess.Process."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.pid = 12345
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# Container creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_sandbox_calls_docker_create() -> None:
    config = _make_config()
    container_id = b"abc123containerid\n"

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        # First call: docker create; second call: docker start
        mock_exec.side_effect = [
            _fake_process(stdout=container_id),
            _fake_process(),
        ]
        mgr = DockerSandboxManager(docker_bin="/usr/bin/docker")
        sandbox = await mgr.create(config)

    assert sandbox.sandbox_id
    assert sandbox.work_dir == "/tmp/test-workdir"

    # Verify docker create was called with expected args
    create_call = mock_exec.call_args_list[0]
    args = create_call[0]
    assert args[0] == "/usr/bin/docker"
    assert args[1] == "create"


@pytest.mark.asyncio
async def test_create_passes_resource_limits() -> None:
    config = _make_config(max_memory_mb=1024, max_cpu_seconds=60)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.side_effect = [
            _fake_process(stdout=b"cid\n"),
            _fake_process(),
        ]
        mgr = DockerSandboxManager(docker_bin="docker")
        await mgr.create(config)

    create_args = list(mock_exec.call_args_list[0][0])
    # Memory limit should appear as --memory 1024m
    mem_idx = create_args.index("--memory")
    assert create_args[mem_idx + 1] == "1024m"
    # CPU limit
    cpu_idx = create_args.index("--cpus")
    assert create_args[cpu_idx + 1] == "60"


@pytest.mark.asyncio
async def test_create_sets_network_none_by_default() -> None:
    config = _make_config(network_access=False)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.side_effect = [
            _fake_process(stdout=b"cid\n"),
            _fake_process(),
        ]
        mgr = DockerSandboxManager(docker_bin="docker")
        await mgr.create(config)

    create_args = list(mock_exec.call_args_list[0][0])
    net_idx = create_args.index("--network")
    assert create_args[net_idx + 1] == "none"


@pytest.mark.asyncio
async def test_create_allows_network_when_configured() -> None:
    config = _make_config(network_access=True)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.side_effect = [
            _fake_process(stdout=b"cid\n"),
            _fake_process(),
        ]
        mgr = DockerSandboxManager(docker_bin="docker")
        await mgr.create(config)

    create_args = list(mock_exec.call_args_list[0][0])
    assert "--network" not in create_args


@pytest.mark.asyncio
async def test_create_uses_read_only_root() -> None:
    config = _make_config()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.side_effect = [
            _fake_process(stdout=b"cid\n"),
            _fake_process(),
        ]
        mgr = DockerSandboxManager(docker_bin="docker")
        await mgr.create(config)

    create_args = list(mock_exec.call_args_list[0][0])
    assert "--read-only" in create_args
    assert "--tmpfs" in create_args


@pytest.mark.asyncio
async def test_create_rejects_bad_config() -> None:
    mgr = DockerSandboxManager(docker_bin="docker")
    with pytest.raises(ValueError, match="max_memory_mb"):
        await mgr.create(_make_config(max_memory_mb=0))
    with pytest.raises(ValueError, match="max_cpu_seconds"):
        await mgr.create(_make_config(max_cpu_seconds=-1))


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_runs_command_in_container() -> None:
    config = _make_config()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.side_effect = [
            _fake_process(stdout=b"cid\n"),  # create
            _fake_process(),  # start
            _fake_process(stdout=b"hello\n", returncode=0),  # exec
        ]
        mgr = DockerSandboxManager(docker_bin="docker")
        sandbox = await mgr.create(config)
        result = await mgr.exec(sandbox.sandbox_id, ["echo", "hello"])

    assert result.stdout == "hello\n"
    assert result.exit_code == 0
    assert not result.timed_out

    exec_call = mock_exec.call_args_list[2][0]
    assert exec_call[0] == "docker"
    assert exec_call[1] == "exec"
    assert "cid" in exec_call  # container id


@pytest.mark.asyncio
async def test_exec_unknown_sandbox_raises() -> None:
    mgr = DockerSandboxManager(docker_bin="docker")
    with pytest.raises(KeyError, match="unknown sandbox_id"):
        await mgr.exec("nonexistent", ["true"])


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destroy_removes_container() -> None:
    config = _make_config()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.side_effect = [
            _fake_process(stdout=b"cid123\n"),  # create
            _fake_process(),  # start
            _fake_process(),  # rm -f
        ]
        mgr = DockerSandboxManager(docker_bin="docker")
        sandbox = await mgr.create(config)
        await mgr.destroy(sandbox.sandbox_id)

    rm_call = mock_exec.call_args_list[2][0]
    assert rm_call[1] == "rm"
    assert rm_call[2] == "-f"
    assert "cid123" in rm_call[3]


@pytest.mark.asyncio
async def test_destroy_idempotent_for_unknown() -> None:
    """Destroying an unknown sandbox should be a no-op."""
    mgr = DockerSandboxManager(docker_bin="docker")
    await mgr.destroy("does-not-exist")  # should not raise


# ---------------------------------------------------------------------------
# is_docker_available
# ---------------------------------------------------------------------------


def test_is_docker_available_true() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert is_docker_available("/usr/bin/docker") is True


def test_is_docker_available_false_on_error() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert is_docker_available("/usr/bin/docker") is False


def test_is_docker_available_false_on_missing_binary() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert is_docker_available("/nonexistent/docker") is False


# ---------------------------------------------------------------------------
# Factory / config flag
# ---------------------------------------------------------------------------


def test_factory_returns_subprocess_by_default() -> None:
    mgr = create_sandbox_manager("subprocess")
    assert isinstance(mgr, SubprocessSandboxManager)


def test_factory_returns_docker_when_available() -> None:
    with patch("silas.execution.sandbox_factory.is_docker_available", return_value=True):
        mgr = create_sandbox_manager("docker")
    assert isinstance(mgr, DockerSandboxManager)


def test_factory_falls_back_when_docker_unavailable() -> None:
    with patch("silas.execution.sandbox_factory.is_docker_available", return_value=False):
        mgr = create_sandbox_manager("docker")
    assert isinstance(mgr, SubprocessSandboxManager)


def test_sandbox_config_backend_field() -> None:
    """SandboxConfig.backend accepts the expected string values."""
    cfg = SandboxConfig(backend="docker")
    assert cfg.backend == "docker"
    cfg2 = SandboxConfig(backend="subprocess")
    assert cfg2.backend == "subprocess"
    cfg3 = SandboxConfig()
    assert cfg3.backend is None
