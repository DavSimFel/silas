from __future__ import annotations

import asyncio
import json
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from silas.models.gates import Gate, GateLane, GateResult


class ScriptChecker:
    """Shell-script gate provider."""

    def __init__(
        self,
        *,
        default_timeout_seconds: float = 30.0,
        working_directory: str | Path | None = None,
    ) -> None:
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be > 0")
        self._default_timeout_seconds = default_timeout_seconds
        self._working_directory = Path(working_directory) if working_directory is not None else None

    async def check(self, gate: Gate, context: dict[str, object]) -> GateResult:
        command = self._resolve_command(gate)
        if not command:
            return self._result(
                gate_name=gate.name,
                action="block",
                reason="script command is required",
                flags=["script_error"],
            )

        timeout_seconds = self._resolve_timeout(gate)
        content = self._extract_content(gate, context)

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._resolve_cwd(gate),
            )
        except OSError as exc:
            return self._result(
                gate_name=gate.name,
                action="block",
                reason=f"failed to start script: {exc}",
                flags=["script_error"],
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(content.encode("utf-8")),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return self._result(
                gate_name=gate.name,
                action="block",
                reason=f"script timed out after {timeout_seconds:.2f}s",
                flags=["script_timeout"],
            )

        output = self._combined_output(stdout, stderr)
        if process.returncode == 0:
            return self._result(
                gate_name=gate.name,
                action="continue",
                reason=output or "script passed",
            )
        if process.returncode == 1:
            return self._result(
                gate_name=gate.name,
                action="block",
                reason=output or "script blocked content",
                flags=["script_block"],
            )
        if process.returncode == 2:
            return self._result(
                gate_name=gate.name,
                action="continue",
                reason=output or "script warning",
                flags=["warn", "script_warn"],
            )

        return self._result(
            gate_name=gate.name,
            action="block",
            reason=output or f"script failed with exit code {process.returncode}",
            flags=["script_error"],
        )

    def _resolve_command(self, gate: Gate) -> list[str]:
        raw = (
            gate.check_command
            or gate.check
            or gate.config.get("command")
            or gate.config.get("script")
        )
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                return []
            return shlex.split(stripped)
        if isinstance(raw, list) and raw and all(isinstance(part, str) and part for part in raw):
            return list(raw)
        return []

    def _resolve_timeout(self, gate: Gate) -> float:
        raw = gate.config.get("timeout_seconds")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
            return float(raw)
        if isinstance(raw, str):
            try:
                parsed = float(raw.strip())
            except ValueError:
                return self._default_timeout_seconds
            if parsed > 0:
                return parsed
        return self._default_timeout_seconds

    def _resolve_cwd(self, gate: Gate) -> str | None:
        raw = gate.config.get("cwd")
        if isinstance(raw, str) and raw.strip():
            return raw
        if self._working_directory is not None:
            return str(self._working_directory)
        return None

    def _extract_content(self, gate: Gate, context: Mapping[str, object]) -> str:
        if isinstance(gate.extract, str) and gate.extract in context:
            return self._as_text(context[gate.extract])
        for key in ("response", "message", "text", "value", "step_output"):
            if key in context:
                return self._as_text(context[key])
        return self._as_text(context)

    def _as_text(self, value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, sort_keys=True)
        if value is None:
            return ""
        return str(value)

    def _combined_output(self, stdout: bytes, stderr: bytes) -> str:
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if out and err:
            return f"{out}\n{err}"
        return out or err

    def _result(
        self,
        *,
        gate_name: str,
        action: Literal["continue", "block", "require_approval"],
        reason: str,
        flags: list[str] | None = None,
    ) -> GateResult:
        return GateResult(
            gate_name=gate_name,
            lane=GateLane.policy,
            action=action,
            reason=reason,
            flags=flags or [],
        )


__all__ = ["ScriptChecker"]
