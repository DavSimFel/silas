from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence

from silas.execution.sandbox import SubprocessSandboxManager
from silas.models.execution import ExecutionEnvelope, ExecutionResult


class ShellExecutor:
    """Runs shell commands inside an isolated subprocess sandbox."""

    def __init__(self, sandbox_manager: SubprocessSandboxManager) -> None:
        self._sandbox_manager = sandbox_manager

    async def execute(self, envelope: ExecutionEnvelope) -> ExecutionResult:
        if envelope.action != "shell_exec":
            return ExecutionResult(
                execution_id=envelope.execution_id,
                step_index=envelope.step_index,
                success=False,
                error=f"unsupported action for shell executor: {envelope.action}",
            )

        sandbox_config = envelope.sandbox_config.model_copy(deep=True)
        sandbox_config.env.update(self._credential_env(envelope.credential_refs))
        sandbox = await self._sandbox_manager.create(sandbox_config)
        command: Sequence[str] | None = None
        try:
            command = self._parse_command(envelope.args)
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
                    error = f"command timed out after {envelope.timeout_seconds}s"
                else:
                    error = (
                        outcome.stderr.strip() or f"command exited with status {outcome.exit_code}"
                    )

            return ExecutionResult(
                execution_id=envelope.execution_id,
                step_index=envelope.step_index,
                success=success,
                return_value=outcome.stdout.strip() or outcome.stderr.strip(),
                metadata={
                    "command": list(command),
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
                metadata={"command": list(command) if command is not None else []},
                error=str(exc),
            )
        finally:
            await self._sandbox_manager.destroy(sandbox.sandbox_id)

    def _parse_command(self, args: dict[str, object]) -> list[str]:
        raw_command = args.get("command")
        if isinstance(raw_command, str):
            parts = shlex.split(raw_command)
        elif isinstance(raw_command, list):
            parts = [str(value) for value in raw_command]
        else:
            raise ValueError("shell executor requires args.command as string or list")

        if not parts:
            raise ValueError("shell command must not be empty")
        return parts

    def _credential_env(self, refs: Mapping[str, str]) -> dict[str, str]:
        return {f"CREDENTIAL_REF_{name.upper()}": ref_id for name, ref_id in refs.items()}


__all__ = ["ShellExecutor"]
