from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from silas.models.approval import ApprovalToken
from silas.models.connections import (
    Connection,
    ConnectionFailure,
    HealthCheckResult,
    SetupStep,
    SetupStepResponse,
)


class SilasConnectionManager:
    def __init__(
        self,
        skills_dir: Path,
        connections_registry: dict[str, Connection] | None = None,
    ) -> None:
        self.skills_dir = Path(skills_dir)
        self._connections: dict[str, Connection] = {}
        self._connection_domains: dict[str, str] = {}
        self._scheduled_refresh: set[str] = set()

        for connection_id, connection in (connections_registry or {}).items():
            self._connections[connection_id] = connection.model_copy(deep=True)

    async def discover_connection(
        self,
        skill_name: str,
        identity_hint: dict[str, object],
    ) -> dict[str, object]:
        script_path = self._resolve_script(skill_name, "discover.py")
        response = await self._run_request_response(
            script_path,
            {"identity_hint": identity_hint},
        )
        return response

    async def run_setup_flow(
        self,
        skill_name: str,
        identity_hint: dict[str, object],
        responses: list[SetupStepResponse] | None = None,
    ) -> list[SetupStep]:
        script_path = self._resolve_script(skill_name, "setup.py")
        process = await self._spawn_script(script_path)
        if process.stdout is None:
            raise RuntimeError("setup script did not expose stdout")

        await self._write_ndjson(
            process.stdin,
            {
                "type": "start",
                "identity_hint": identity_hint,
            },
        )

        response_queue: deque[SetupStepResponse] = deque(responses or [])
        setup_steps: list[SetupStep] = []

        while True:
            line = await process.stdout.readline()
            if not line:
                break

            event = _parse_ndjson_line(line.decode("utf-8", errors="replace"))
            if event is None:
                continue

            event_type = str(event.get("type", ""))

            # Dispatch each NDJSON event type to its handler
            if event_type == "await_input":
                step_id = str(event.get("step_id", ""))
                response = (
                    response_queue.popleft()
                    if response_queue
                    else SetupStepResponse(step_type=step_id or "unknown", action="done")
                )
                await self._write_ndjson(
                    process.stdin,
                    {
                        "type": "step_result",
                        "step_id": step_id,
                        "payload": response.model_dump(mode="json"),
                    },
                )
                continue

            # All other event types produce a SetupStep
            step = self._parse_setup_event(event_type, event, skill_name)
            if step is not None:
                setup_steps.append(step)
                continue

            try:
                setup_steps.append(SetupStep.model_validate(event))
            except ValidationError:
                continue

        if process.stdin is not None:
            process.stdin.close()

        stderr_text = await _read_stderr(process)
        return_code = await process.wait()
        if return_code != 0:
            message = stderr_text or "setup script failed"
            raise RuntimeError(message)

        return setup_steps

    async def activate_connection(
        self,
        skill_name: str,
        provider: str,
        auth_payload: dict[str, object],
        approval: ApprovalToken | None = None,
    ) -> str:
        del approval
        now = datetime.now(UTC)

        raw_connection_id = auth_payload.get("connection_id")
        if isinstance(raw_connection_id, str) and raw_connection_id.strip():
            connection_id = raw_connection_id
        else:
            connection_id = f"{skill_name}-{uuid.uuid4().hex}"

        raw_status = auth_payload.get("status", "active")
        status = str(raw_status) if str(raw_status) in {"active", "inactive", "error"} else "active"
        connection = Connection(
            connection_id=connection_id,
            skill_name=skill_name,
            provider=provider,
            status=status,
            permissions_granted=_as_str_list(auth_payload.get("permissions_granted")),
            token_expires_at=_to_datetime(
                auth_payload.get("token_expires_at") or auth_payload.get("new_expires_at")
            ),
            created_at=now,
            updated_at=now,
        )
        self._connections[connection_id] = connection

        domain = auth_payload.get("domain")
        if isinstance(domain, str) and domain.strip():
            self._connection_domains[connection_id] = domain

        return connection_id

    async def escalate_permission(
        self,
        connection_id: str,
        requested_permissions: list[str],
        reason: str,
        channel: object | None = None,
        recipient_id: str | None = None,
    ) -> bool:
        del reason, channel, recipient_id
        connection = self._connections.get(connection_id)
        if connection is None:
            return False

        merged_permissions = list(
            dict.fromkeys([*connection.permissions_granted, *_as_str_list(requested_permissions)])
        )
        now = datetime.now(UTC)
        self._connections[connection_id] = connection.model_copy(
            update={
                "permissions_granted": merged_permissions,
                "updated_at": now,
            }
        )
        return True

    async def run_health_checks(self) -> list[HealthCheckResult]:
        results: list[HealthCheckResult] = []
        for connection_id in sorted(self._connections):
            connection = self._connections[connection_id]
            if connection.status != "active":
                continue

            now = datetime.now(UTC)
            try:
                script_path = self._resolve_script(connection.skill_name, "health_check.py")
                response = await self._run_request_response(
                    script_path,
                    {"connection_id": connection_id},
                )
                health = HealthCheckResult.model_validate(response)
            except (FileNotFoundError, RuntimeError, ValidationError) as exc:
                health = HealthCheckResult(
                    healthy=False,
                    error=str(exc),
                    warnings=[],
                )

            updates: dict[str, object] = {
                "last_health_check": now,
                "updated_at": now,
                "status": "active" if health.healthy else "error",
            }
            if health.token_expires_at is not None:
                updates["token_expires_at"] = health.token_expires_at

            self._connections[connection_id] = connection.model_copy(update=updates)
            results.append(health)
            await self.schedule_proactive_refresh(connection_id, health)

        return results

    async def schedule_proactive_refresh(
        self,
        connection_id: str,
        health: HealthCheckResult | None = None,
    ) -> None:
        connection = self._connections.get(connection_id)
        if connection is None:
            return

        expires_at = health.token_expires_at if health is not None else connection.token_expires_at
        if expires_at is None:
            self._scheduled_refresh.discard(connection_id)
            return

        if expires_at - datetime.now(UTC) <= timedelta(minutes=10):
            self._scheduled_refresh.add(connection_id)
        else:
            self._scheduled_refresh.discard(connection_id)

    async def refresh_token(self, connection_id: str) -> bool:
        connection = self._connections.get(connection_id)
        if connection is None:
            return False

        now = datetime.now(UTC)
        try:
            script_path = self._resolve_script(connection.skill_name, "refresh_token.py")
            response = await self._run_request_response(
                script_path,
                {"connection_id": connection_id},
            )
        except (FileNotFoundError, RuntimeError):
            self._connections[connection_id] = connection.model_copy(
                update={"status": "error", "updated_at": now}
            )
            self._scheduled_refresh.discard(connection_id)
            return False

        success = bool(response.get("success", False))
        if not success:
            self._connections[connection_id] = connection.model_copy(
                update={"status": "error", "updated_at": now}
            )
            return False

        new_expires_at = _to_datetime(
            response.get("new_expires_at") or response.get("token_expires_at")
        )
        updates: dict[str, object] = {
            "status": "active",
            "last_refresh": now,
            "updated_at": now,
        }
        if new_expires_at is not None:
            updates["token_expires_at"] = new_expires_at

        self._connections[connection_id] = connection.model_copy(update=updates)
        self._scheduled_refresh.discard(connection_id)
        return True

    async def recover(self, connection_id: str) -> tuple[bool, str]:
        connection = self._connections.get(connection_id)
        if connection is None:
            return False, "connection not found"

        now = datetime.now(UTC)
        try:
            script_path = self._resolve_script(connection.skill_name, "recover.py")
            response = await self._run_request_response(
                script_path,
                {"connection_id": connection_id},
            )
        except (FileNotFoundError, RuntimeError) as exc:
            self._connections[connection_id] = connection.model_copy(
                update={"status": "error", "updated_at": now}
            )
            return False, str(exc)

        if "success" in response:
            success = bool(response.get("success", False))
            message = response.get("message")
            text = str(message) if message is not None else ("recovered" if success else "recovery failed")
            self._connections[connection_id] = connection.model_copy(
                update={
                    "status": "active" if success else "error",
                    "updated_at": now,
                }
            )
            return success, text

        failure = self._parse_failure(response.get("failure", response), connection.provider)
        self._connections[connection_id] = connection.model_copy(
            update={"status": "error", "updated_at": now}
        )
        return False, failure.message

    async def list_connections(self, domain: str | None = None) -> list[Connection]:
        connections = [
            self._connections[connection_id].model_copy(deep=True)
            for connection_id in sorted(self._connections)
        ]
        if domain is None:
            return connections

        domain_lower = domain.lower()
        filtered: list[Connection] = []
        for connection in connections:
            stored_domain = self._connection_domains.get(connection.connection_id)
            if stored_domain is not None and stored_domain.lower() == domain_lower:
                filtered.append(connection)
                continue

            if stored_domain is None and (
                domain_lower in connection.skill_name.lower()
                or domain_lower in connection.provider.lower()
            ):
                filtered.append(connection)

        return filtered

    @property
    def scheduled_refreshes(self) -> set[str]:
        return set(self._scheduled_refresh)

    def _resolve_script(self, skill_name: str, script_name: str) -> Path:
        skills_root = self.skills_dir.resolve()
        skill_dir = (self.skills_dir / skill_name).resolve()
        try:
            # Reject traversal in skill_name (e.g. "../other-skill") so all
            # connection scripts stay scoped under the configured skills root.
            skill_dir.relative_to(skills_root)
        except ValueError as exc:
            raise FileNotFoundError(f"skill directory not found: {skill_name}") from exc

        if not skill_dir.exists() or not skill_dir.is_dir():
            raise FileNotFoundError(f"skill directory not found: {skill_name}")

        candidates = [
            skill_dir / script_name,
            skill_dir / "scripts" / script_name,
        ]
        for path in candidates:
            resolved_path = path.resolve()
            try:
                # Reject traversal in script names (e.g. "../../outside.py")
                # even if the target exists on disk.
                resolved_path.relative_to(skill_dir)
            except ValueError:
                continue

            if resolved_path.exists() and resolved_path.is_file():
                return resolved_path

        raise FileNotFoundError(f"{script_name} not found for skill: {skill_name}")

    async def _spawn_script(self, script_path: Path) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(script_path.parent),
        )

    async def _run_request_response(
        self,
        script_path: Path,
        request_payload: dict[str, object],
    ) -> dict[str, object]:
        process = await self._spawn_script(script_path)
        request_line = f"{json.dumps(request_payload, separators=(',', ':'))}\n".encode()
        stdout, stderr = await process.communicate(request_line)
        if process.returncode != 0:
            details = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(details or f"script failed: {script_path.name}")

        events: list[dict[str, object]] = []
        for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
            parsed = _parse_ndjson_line(raw_line)
            if parsed is not None:
                events.append(parsed)

        if not events:
            return {}

        return events[-1]

    async def _write_ndjson(
        self,
        stream: asyncio.StreamWriter | None,
        payload: dict[str, object],
    ) -> None:
        if stream is None:
            return
        line = f"{json.dumps(payload, separators=(',', ':'))}\n"
        stream.write(line.encode("utf-8"))
        await stream.drain()

    def _parse_setup_event(
        self, event_type: str, event: dict[str, object], skill_name: str,
    ) -> SetupStep | None:
        """Convert a single NDJSON setup event into a SetupStep, or None if unrecognized."""
        if event_type == "setup_step":
            step_payload = event.get("step")
            if isinstance(step_payload, dict):
                return SetupStep.model_validate(step_payload)
            return None

        if event_type == "completion":
            step_payload = event.get("step")
            if isinstance(step_payload, dict):
                return SetupStep.model_validate(step_payload)
            summary = event.get("summary")
            return SetupStep(
                type="completion",
                success=bool(event.get("success", True)),
                summary=str(summary) if summary is not None else "Setup completed",
                permissions_granted=_as_str_list(event.get("permissions_granted")),
            )

        if event_type == "failure":
            return SetupStep(
                type="failure",
                failure=self._parse_failure(event.get("failure"), skill_name),
            )

        if event_type == "progress":
            progress_pct = event.get("progress_pct")
            return SetupStep(
                type="progress",
                message=str(event.get("message")) if event.get("message") is not None else None,
                progress_pct=float(progress_pct) if isinstance(progress_pct, int | float) else None,
            )

        return None

    def _parse_failure(self, payload: object, service: str) -> ConnectionFailure:
        if isinstance(payload, dict):
            try:
                return ConnectionFailure.model_validate(payload)
            except ValidationError:
                pass

        return ConnectionFailure(
            failure_type="unknown",
            service=service,
            message="connection operation failed",
            recovery_options=[],
        )


def _parse_ndjson_line(line: str) -> dict[str, object] | None:
    cleaned = line.strip()
    if not cleaned:
        return None
    try:
        decoded = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def _to_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


async def _read_stderr(process: asyncio.subprocess.Process) -> str:
    if process.stderr is None:
        return ""
    data = await process.stderr.read()
    return data.decode("utf-8", errors="replace").strip()


__all__ = ["SilasConnectionManager"]
