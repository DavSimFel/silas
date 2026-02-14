from __future__ import annotations

import sys
from pathlib import Path

import pytest
from silas.execution.sandbox import SubprocessSandboxManager
from silas.models.execution import SandboxConfig


@pytest.mark.asyncio
async def test_create_returns_sandbox_and_workdir_exists(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    sandbox = await manager.create(SandboxConfig(work_dir=str(tmp_path / "work")))

    assert sandbox.sandbox_id
    assert Path(sandbox.work_dir).exists()


@pytest.mark.asyncio
async def test_create_uses_unique_workdirs(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    config = SandboxConfig(work_dir=str(tmp_path / "work"))

    first = await manager.create(config)
    second = await manager.create(config)
    try:
        assert first.sandbox_id != second.sandbox_id
        assert first.work_dir != second.work_dir
    finally:
        await manager.destroy(first.sandbox_id)
        await manager.destroy(second.sandbox_id)


@pytest.mark.asyncio
async def test_destroy_removes_workdir(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    sandbox = await manager.create(SandboxConfig(work_dir=str(tmp_path / "work")))
    sandbox_dir = Path(sandbox.work_dir)

    await manager.destroy(sandbox.sandbox_id)
    assert not sandbox_dir.exists()


@pytest.mark.asyncio
async def test_destroy_unknown_sandbox_is_noop(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    await manager.destroy("missing-id")


@pytest.mark.asyncio
async def test_exec_runs_command_and_captures_stdout(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    sandbox = await manager.create(SandboxConfig(work_dir=str(tmp_path / "work")))
    try:
        result = await manager.exec(
            sandbox.sandbox_id,
            [sys.executable, "-c", "print('hello from sandbox')"],
            timeout_seconds=5,
        )
        assert result.exit_code == 0
        assert result.timed_out is False
        assert "hello from sandbox" in result.stdout
    finally:
        await manager.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_exec_sets_cwd_to_sandbox_workdir(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    sandbox = await manager.create(SandboxConfig(work_dir=str(tmp_path / "work")))
    try:
        result = await manager.exec(
            sandbox.sandbox_id,
            [sys.executable, "-c", "import os; print(os.getcwd())"],
            timeout_seconds=5,
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == str(Path(sandbox.work_dir))
    finally:
        await manager.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_exec_respects_timeout(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    sandbox = await manager.create(SandboxConfig(work_dir=str(tmp_path / "work")))
    try:
        result = await manager.exec(
            sandbox.sandbox_id,
            [sys.executable, "-c", "import time; time.sleep(2)"],
            timeout_seconds=1,
        )
        assert result.timed_out is True
    finally:
        await manager.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_exec_isolates_working_dirs_between_sandboxes(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    config = SandboxConfig(work_dir=str(tmp_path / "work"))
    first = await manager.create(config)
    second = await manager.create(config)
    try:
        create_file = (
            "from pathlib import Path; Path('only_here.txt').write_text('ok', encoding='utf-8')"
        )
        await manager.exec(first.sandbox_id, [sys.executable, "-c", create_file], timeout_seconds=5)

        first_check = await manager.exec(
            first.sandbox_id,
            [
                sys.executable,
                "-c",
                "from pathlib import Path; print(Path('only_here.txt').exists())",
            ],
            timeout_seconds=5,
        )
        second_check = await manager.exec(
            second.sandbox_id,
            [
                sys.executable,
                "-c",
                "from pathlib import Path; print(Path('only_here.txt').exists())",
            ],
            timeout_seconds=5,
        )

        assert first_check.stdout.strip() == "True"
        assert second_check.stdout.strip() == "False"
    finally:
        await manager.destroy(first.sandbox_id)
        await manager.destroy(second.sandbox_id)


@pytest.mark.asyncio
async def test_exec_injects_environment_variables(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    sandbox = await manager.create(
        SandboxConfig(
            work_dir=str(tmp_path / "work"),
            env={"HELLO": "world"},
        )
    )
    try:
        result = await manager.exec(
            sandbox.sandbox_id,
            [sys.executable, "-c", "import os; print(os.getenv('HELLO', 'missing'))"],
            timeout_seconds=5,
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == "world"
    finally:
        await manager.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_exec_unknown_sandbox_raises_key_error(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    with pytest.raises(KeyError):
        await manager.exec("unknown", [sys.executable, "-c", "print('x')"])


@pytest.mark.asyncio
async def test_create_allows_network_access_when_requested(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    sandbox = await manager.create(
        SandboxConfig(work_dir=str(tmp_path / "work"), network_access=True)
    )
    try:
        result = await manager.exec(
            sandbox.sandbox_id,
            [sys.executable, "-c", "print('ok')"],
            timeout_seconds=5,
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == "ok"
    finally:
        await manager.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_create_fails_closed_when_network_isolation_unavailable(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    manager._unshare_bin = None  # type: ignore[attr-defined]
    manager._network_isolation_checked = False  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match="network isolation unavailable"):
        await manager.create(SandboxConfig(work_dir=str(tmp_path / "work"), network_access=False))


@pytest.mark.asyncio
async def test_exec_rejects_shell_dash_c(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    sandbox = await manager.create(SandboxConfig(work_dir=str(tmp_path / "work")))
    try:
        with pytest.raises(ValueError, match="shell '-c'"):
            await manager.exec(
                sandbox.sandbox_id,
                ["bash", "-c", "echo hi"],
                timeout_seconds=5,
            )
    finally:
        await manager.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_exec_does_not_inherit_host_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SILAS_APPROVAL_KEY", "super-secret")
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    sandbox = await manager.create(SandboxConfig(work_dir=str(tmp_path / "work")))
    try:
        result = await manager.exec(
            sandbox.sandbox_id,
            [
                sys.executable,
                "-c",
                "import os; print(os.getenv('SILAS_APPROVAL_KEY', 'missing'))",
            ],
            timeout_seconds=5,
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == "missing"
    finally:
        await manager.destroy(sandbox.sandbox_id)


@pytest.mark.asyncio
async def test_create_rejects_invalid_limits(tmp_path: Path) -> None:
    manager = SubprocessSandboxManager(base_dir=tmp_path)
    with pytest.raises(ValueError, match="max_memory_mb"):
        await manager.create(SandboxConfig(work_dir=str(tmp_path / "work"), max_memory_mb=0))

    with pytest.raises(ValueError, match="max_cpu_seconds"):
        await manager.create(SandboxConfig(work_dir=str(tmp_path / "work"), max_cpu_seconds=0))
