from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from silas.execution.sandbox import SubprocessSandboxManager
from silas.models.execution import SandboxConfig, VerificationReport, VerificationResult
from silas.models.work import Expectation, VerificationCheck


class SilasVerificationRunner:
    """Runs deterministic verification checks in a dedicated sandbox."""

    def __init__(
        self,
        sandbox_manager: SubprocessSandboxManager,
        verify_dir: str | Path = "./data/sandbox/verify",
        project_dirs: Sequence[str | Path] | None = None,
    ) -> None:
        self._sandbox_manager = sandbox_manager
        self._verify_dir = Path(verify_dir).resolve()
        self._verify_dir.mkdir(parents=True, exist_ok=True)
        extra_dirs = [Path(path).resolve() for path in (project_dirs or [])]
        self._allowed_roots = [self._verify_dir, *extra_dirs]

    async def run_checks(self, checks: list[VerificationCheck]) -> VerificationReport:
        results: list[VerificationResult] = []
        for check in checks:
            results.append(await self._run_check(check))

        failed = [result for result in results if not result.passed]
        return VerificationReport(
            all_passed=not failed,
            results=results,
            failed=failed,
            timestamp=datetime.now(timezone.utc),
        )

    async def _run_check(self, check: VerificationCheck) -> VerificationResult:
        sandbox_id: str | None = None
        try:
            sandbox = await self._sandbox_manager.create(
                SandboxConfig(
                    work_dir=str(self._verify_dir),
                    network_access=check.network,
                )
            )
            sandbox_id = sandbox.sandbox_id

            command = ["/bin/bash", "-lc", check.run]
            execution = await self._sandbox_manager.exec(
                sandbox_id,
                command,
                timeout_seconds=check.timeout,
            )
            output = self._merge_output(execution.stdout, execution.stderr)
            output = self._truncate(output)

            if execution.timed_out:
                return VerificationResult(
                    name=check.name,
                    passed=False,
                    reason=f"timed out after {check.timeout}s",
                    output=output,
                    exit_code=execution.exit_code,
                )

            passed, reason = self._evaluate(check.expect, output, execution.exit_code)
            return VerificationResult(
                name=check.name,
                passed=passed,
                reason=reason,
                output=output,
                exit_code=execution.exit_code,
            )
        except Exception as exc:
            return VerificationResult(
                name=check.name,
                passed=False,
                reason=str(exc),
            )
        finally:
            if sandbox_id is not None:
                await self._sandbox_manager.destroy(sandbox_id)

    def _evaluate(
        self,
        expect: Expectation,
        output: str,
        exit_code: int | None,
    ) -> tuple[bool, str]:
        normalized = output.strip()

        if expect.exit_code is not None:
            passed = exit_code == expect.exit_code
            return passed, self._reason(passed, f"expected exit_code={expect.exit_code}, got {exit_code}")

        if expect.equals is not None:
            passed = normalized == expect.equals
            return passed, self._reason(passed, "output mismatch")

        if expect.contains is not None:
            passed = expect.contains in normalized
            return passed, self._reason(passed, f"output missing substring {expect.contains!r}")

        if expect.regex is not None:
            try:
                matched = re.search(expect.regex, normalized, flags=re.MULTILINE) is not None
            except re.error as exc:
                return False, f"invalid regex: {exc}"
            return matched, self._reason(matched, f"output does not match regex {expect.regex!r}")

        if expect.output_lt is not None:
            parsed = self._parse_float(normalized)
            if parsed is None:
                return False, "output is not numeric"
            passed = parsed < expect.output_lt
            return passed, self._reason(passed, f"expected output < {expect.output_lt}, got {parsed}")

        if expect.output_gt is not None:
            parsed = self._parse_float(normalized)
            if parsed is None:
                return False, "output is not numeric"
            passed = parsed > expect.output_gt
            return passed, self._reason(passed, f"expected output > {expect.output_gt}, got {parsed}")

        if expect.file_exists is not None:
            try:
                path = self._resolve_permitted_path(expect.file_exists)
            except ValueError as exc:
                return False, str(exc)
            passed = path.exists()
            return passed, self._reason(passed, f"file does not exist: {path}")

        if expect.not_empty:
            passed = normalized != ""
            return passed, self._reason(passed, "output is empty")

        return False, "unsupported expectation"

    def _resolve_permitted_path(self, raw_path: str) -> Path:
        input_path = Path(raw_path)
        if ".." in input_path.parts:
            raise ValueError("Path outside permitted directories")

        candidate = (self._verify_dir / input_path).resolve() if not input_path.is_absolute() else input_path.resolve()
        if not any(self._is_relative_to(candidate, root) for root in self._allowed_roots):
            raise ValueError("Path outside permitted directories")
        return candidate

    def _is_relative_to(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _parse_float(self, value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None

    def _merge_output(self, stdout: str, stderr: str) -> str:
        if stdout and stderr:
            return f"{stdout}\n{stderr}"
        return stdout or stderr

    def _truncate(self, text: str, max_chars: int = 1000) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    def _reason(self, passed: bool, fail_reason: str) -> str:
        if passed:
            return "passed"
        return fail_reason


__all__ = ["SilasVerificationRunner"]
