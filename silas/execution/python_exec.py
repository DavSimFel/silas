from __future__ import annotations

import sys
import uuid
from collections.abc import Mapping
from pathlib import Path

from silas.execution.sandbox import SubprocessSandboxManager
from silas.models.execution import ExecutionEnvelope, ExecutionResult


class PythonExecutor:
    """Runs Python scripts inside an isolated subprocess sandbox."""

    def __init__(
        self,
        sandbox_manager: SubprocessSandboxManager,
        python_bin: str | None = None,
    ) -> None:
        self._sandbox_manager = sandbox_manager
        self._python_bin = python_bin or sys.executable

    async def execute(self, envelope: ExecutionEnvelope) -> ExecutionResult:
        if envelope.action != "python_exec":
            return ExecutionResult(
                execution_id=envelope.execution_id,
                step_index=envelope.step_index,
                success=False,
                error=f"unsupported action for python executor: {envelope.action}",
            )

        sandbox_config = envelope.sandbox_config.model_copy(deep=True)
        sandbox_config.env.update(self._credential_env(envelope.credential_refs))
        sandbox = await self._sandbox_manager.create(sandbox_config)
        command: list[str] | None = None
        try:
            script_path = self._materialize_script(sandbox.work_dir, envelope.args)
            argv = self._argv(envelope.args)
            command = [self._python_bin, script_path, *argv]

            outcome = await self._sandbox_manager.exec(
                sandbox.sandbox_id,
                command,
                timeout_seconds=envelope.timeout_seconds,
                max_output_bytes=envelope.max_output_bytes,
            )
            success = not outcome.timed_out and outcome.exit_code == 0
            error = None
            if not success:
                if outcome.timed_out:
                    error = f"python script timed out after {envelope.timeout_seconds}s"
                else:
                    error = (
                        outcome.stderr.strip() or f"script exited with status {outcome.exit_code}"
                    )

            return ExecutionResult(
                execution_id=envelope.execution_id,
                step_index=envelope.step_index,
                success=success,
                return_value=outcome.stdout.strip() or outcome.stderr.strip(),
                metadata={
                    "script_path": script_path,
                    "exit_code": outcome.exit_code,
                    "timed_out": outcome.timed_out,
                },
                error=error,
                duration_seconds=outcome.duration_seconds,
            )
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            return ExecutionResult(
                execution_id=envelope.execution_id,
                step_index=envelope.step_index,
                success=False,
                metadata={"command": command or []},
                error=str(exc),
            )
        finally:
            await self._sandbox_manager.destroy(sandbox.sandbox_id)

    def _materialize_script(self, work_dir: str, args: dict[str, object]) -> str:
        inline_script = args.get("script")
        script_path = args.get("script_path")

        if isinstance(inline_script, str):
            target = Path(work_dir) / f"script-{uuid.uuid4().hex}.py"
            target.write_text(inline_script, encoding="utf-8")
            return str(target)

        if isinstance(script_path, str):
            return str(self._resolve_script_path(work_dir, script_path))

        raise ValueError("python executor requires args.script or args.script_path")

    def _resolve_script_path(self, work_dir: str, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if ".." in candidate.parts:
            raise ValueError("script_path must not contain '..'")

        base = Path(work_dir).resolve()
        resolved = (
            (base / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        )
        if not self._is_relative_to(resolved, base):
            raise ValueError("script_path must remain inside sandbox work_dir")
        if not resolved.exists():
            raise FileNotFoundError(f"script_path does not exist: {resolved}")
        return resolved

    def _argv(self, args: dict[str, object]) -> list[str]:
        raw_argv = args.get("argv", [])
        if not isinstance(raw_argv, list):
            raise ValueError("args.argv must be a list of values")
        return [str(value) for value in raw_argv]

    def _credential_env(self, refs: Mapping[str, str]) -> dict[str, str]:
        return {f"CREDENTIAL_REF_{name.upper()}": ref_id for name, ref_id in refs.items()}

    def _is_relative_to(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False


__all__ = ["PythonExecutor"]
