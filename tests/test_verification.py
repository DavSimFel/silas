from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest
from silas.execution.sandbox import SubprocessSandboxManager
from silas.execution.verification_runner import SilasVerificationRunner
from silas.models.work import Expectation, VerificationCheck


def _python_cmd(source: str) -> str:
    return f"{sys.executable} -c {shlex.quote(source)}"


def _check(
    name: str,
    run: str,
    expect: Expectation,
    *,
    timeout: int = 5,
    network: bool = False,
) -> VerificationCheck:
    return VerificationCheck(name=name, run=run, expect=expect, timeout=timeout, network=network)


@pytest.fixture
def verify_dir(tmp_path: Path) -> Path:
    path = tmp_path / "verify"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def runner(tmp_path: Path, verify_dir: Path) -> SilasVerificationRunner:
    sandbox_manager = SubprocessSandboxManager(base_dir=tmp_path / "sandboxes")
    return SilasVerificationRunner(sandbox_manager=sandbox_manager, verify_dir=verify_dir)


@pytest.mark.asyncio
async def test_exit_code_predicate_passes(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("exit-ok", _python_cmd("import sys; sys.exit(0)"), Expectation(exit_code=0))]
    )
    assert report.all_passed is True
    assert report.results[0].passed is True


@pytest.mark.asyncio
async def test_exit_code_predicate_failure(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("exit-fail", _python_cmd("import sys; sys.exit(2)"), Expectation(exit_code=0))]
    )
    assert report.all_passed is False
    assert report.results[0].passed is False
    assert report.failed[0].name == "exit-fail"


@pytest.mark.asyncio
async def test_equals_predicate_passes(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("equals", _python_cmd("print('done')"), Expectation(equals="done"))]
    )
    assert report.all_passed is True


@pytest.mark.asyncio
async def test_contains_predicate_failure(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("contains", _python_cmd("print('alpha')"), Expectation(contains="beta"))]
    )
    assert report.all_passed is False
    assert "substring" in report.results[0].reason


@pytest.mark.asyncio
async def test_regex_predicate_passes(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("regex", _python_cmd("print('job-123')"), Expectation(regex=r"job-\d+"))]
    )
    assert report.all_passed is True


@pytest.mark.asyncio
async def test_output_lt_predicate_passes(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("lt", _python_cmd("print(4.5)"), Expectation(output_lt=5.0))]
    )
    assert report.results[0].passed is True


@pytest.mark.asyncio
async def test_output_gt_predicate_failure(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("gt", _python_cmd("print(1.2)"), Expectation(output_gt=3.0))]
    )
    assert report.results[0].passed is False
    assert "expected output >" in report.results[0].reason


@pytest.mark.asyncio
async def test_not_empty_predicate_passes(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("not-empty", _python_cmd("print('x')"), Expectation(not_empty=True))]
    )
    assert report.results[0].passed is True


@pytest.mark.asyncio
async def test_not_empty_predicate_failure(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("empty", _python_cmd("pass"), Expectation(not_empty=True))]
    )
    assert report.results[0].passed is False
    assert "empty" in report.results[0].reason


@pytest.mark.asyncio
async def test_file_exists_passes_with_allowed_relative_path(
    runner: SilasVerificationRunner,
    verify_dir: Path,
) -> None:
    (verify_dir / "artifact.txt").write_text("ready", encoding="utf-8")
    report = await runner.run_checks(
        [_check("file", "true", Expectation(file_exists="artifact.txt"))]
    )
    assert report.results[0].passed is True


@pytest.mark.asyncio
async def test_file_exists_fails_when_missing(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("file-missing", "true", Expectation(file_exists="missing.txt"))]
    )
    assert report.results[0].passed is False
    assert "does not exist" in report.results[0].reason


@pytest.mark.asyncio
async def test_file_exists_rejects_parent_traversal(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("file-traversal", "true", Expectation(file_exists="../secrets.txt"))]
    )
    assert report.results[0].passed is False
    assert "outside permitted directories" in report.results[0].reason.lower()


@pytest.mark.asyncio
async def test_file_exists_rejects_absolute_outside_path(
    runner: SilasVerificationRunner,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    report = await runner.run_checks(
        [_check("file-outside", "true", Expectation(file_exists=str(outside)))]
    )
    assert report.results[0].passed is False
    assert "outside permitted directories" in report.results[0].reason.lower()


@pytest.mark.asyncio
async def test_output_is_truncated_to_1000_chars(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [_check("truncate", _python_cmd("print('x' * 1500)"), Expectation(contains="xxx"))]
    )
    assert report.results[0].passed is True
    assert len(report.results[0].output) == 1000


@pytest.mark.asyncio
async def test_multiple_checks_populates_failed_list(runner: SilasVerificationRunner) -> None:
    report = await runner.run_checks(
        [
            _check("ok", _python_cmd("print('a')"), Expectation(contains="a")),
            _check("bad", _python_cmd("print('a')"), Expectation(contains="b")),
        ]
    )
    assert report.all_passed is False
    assert len(report.results) == 2
    assert len(report.failed) == 1
    assert report.failed[0].name == "bad"
