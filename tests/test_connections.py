from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from silas.connections.manager import SilasConnectionManager
from silas.models.connections import (
    Connection,
    ConnectionFailure,
    HealthCheckResult,
    RecoveryOption,
    SecureInputCompleted,
    SecureInputRequest,
    SetupStep,
    SetupStepResponse,
)
from silas.persistence.connection_store import SQLiteConnectionStore
from silas.persistence.migrations import run_migrations


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _touch_script(skills_dir: Path, skill_name: str, script_name: str) -> Path:
    path = skills_dir / skill_name / script_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return path


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return

    def close(self) -> None:
        self.closed = True


class _FakeStdout:
    def __init__(self, lines: list[str] | None = None) -> None:
        self._lines = []
        for line in lines or []:
            normalized = line if line.endswith("\n") else f"{line}\n"
            self._lines.append(normalized.encode("utf-8"))

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeStderr:
    def __init__(self, content: str = "") -> None:
        self._content = content.encode("utf-8")

    async def read(self) -> bytes:
        content = self._content
        self._content = b""
        return content


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout_lines: list[str] | None = None,
        stderr_text: str = "",
        returncode: int = 0,
    ) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(stdout_lines)
        self.stderr = _FakeStderr(stderr_text)
        self.returncode = returncode
        self.communicated_input: bytes | None = None
        self._stdout_payload = "".join(
            f"{line}\n" if not line.endswith("\n") else line for line in (stdout_lines or [])
        )
        self._stderr_payload = stderr_text

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.communicated_input = input
        return self._stdout_payload.encode("utf-8"), self._stderr_payload.encode("utf-8")

    async def wait(self) -> int:
        return self.returncode


class TestConnectionModels:
    def test_connection_creation_and_serialization(self) -> None:
        now = _now()
        connection = Connection(
            connection_id="conn-1",
            skill_name="github",
            provider="GitHub",
            permissions_granted=["repo", "read:user"],
            created_at=now,
            updated_at=now,
        )

        payload = connection.model_dump(mode="json")
        restored = Connection.model_validate(payload)
        assert restored.connection_id == "conn-1"
        assert restored.permissions_granted == ["repo", "read:user"]
        assert isinstance(payload["created_at"], str)

    def test_health_check_result_all_fields(self) -> None:
        now = _now()
        result = HealthCheckResult(
            healthy=False,
            token_expires_at=now + timedelta(hours=1),
            refresh_token_expires_at=now + timedelta(days=10),
            latency_ms=123,
            error="token near expiry",
            warnings=["refresh token within 14 days"],
        )
        assert result.healthy is False
        assert result.latency_ms == 123
        assert result.error == "token near expiry"
        assert result.warnings == ["refresh token within 14 days"]

    def test_connection_failure_with_recovery_options(self) -> None:
        failure = ConnectionFailure(
            failure_type="enterprise_policy_block",
            service="Microsoft 365",
            message="Admin approval required",
            recovery_options=[
                RecoveryOption(
                    action="request_admin_approval",
                    label="Ask admin",
                    description="Create an admin approval request",
                    risk_level="low",
                )
            ],
        )
        assert failure.failure_type == "enterprise_policy_block"
        assert len(failure.recovery_options) == 1
        assert failure.recovery_options[0].action == "request_admin_approval"

    def test_setup_step_device_code_variant(self) -> None:
        step = SetupStep(
            type="device_code",
            verification_url="https://microsoft.com/devicelogin",
            user_code="ABCD-1234",
            expires_in=900,
            poll_interval=5,
        )
        assert step.type == "device_code"
        assert step.user_code == "ABCD-1234"

    def test_setup_step_secure_input_variant(self) -> None:
        request = SecureInputRequest(
            ref_id="ref-1",
            label="GitHub PAT",
            input_hint="ghp_...",
            guidance={"instructions": "Paste token"},
        )
        step = SetupStep(type="secure_input", request=request)
        assert step.type == "secure_input"
        assert step.request is not None
        assert step.request.ref_id == "ref-1"

    def test_setup_step_completion_variant(self) -> None:
        step = SetupStep(
            type="completion",
            success=True,
            summary="Connected successfully",
            permissions_granted=["repo"],
        )
        assert step.type == "completion"
        assert step.success is True
        assert step.permissions_granted == ["repo"]

    def test_setup_step_failure_variant(self) -> None:
        failure = ConnectionFailure(
            failure_type="rate_limited",
            service="GitHub",
            message="Retry in a minute",
        )
        step = SetupStep(type="failure", failure=failure)
        assert step.type == "failure"
        assert step.failure is not None
        assert step.failure.failure_type == "rate_limited"

    def test_secure_input_request_completed_roundtrip(self) -> None:
        request = SecureInputRequest(
            ref_id="ref-2",
            label="Notion token",
            guidance={"instructions": "Use internal integration token"},
        )
        request_payload = request.model_dump(mode="json")
        restored_request = SecureInputRequest.model_validate(request_payload)
        assert restored_request.ref_id == "ref-2"

        completed = SecureInputCompleted(ref_id="ref-2", success=True)
        completed_payload = completed.model_dump(mode="json")
        restored_completed = SecureInputCompleted.model_validate(completed_payload)
        assert restored_completed.success is True

    @pytest.mark.parametrize("action", ["done", "cancel", "trouble", "retry"])
    def test_setup_step_response_actions(self, action: str) -> None:
        response = SetupStepResponse(step_type="device_code", action=action)
        assert response.action == action

    @pytest.mark.parametrize("risk", ["low", "medium", "high"])
    def test_recovery_option_risk_levels(self, risk: str) -> None:
        option = RecoveryOption(
            action="retry",
            label="Retry",
            description="Try the setup again",
            risk_level=risk,
        )
        assert option.risk_level == risk


class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_discover_connection_uses_subprocess(self, tmp_path: Path, monkeypatch) -> None:
        skills_dir = tmp_path / "skills"
        _touch_script(skills_dir, "github", "discover.py")
        fake_process = _FakeProcess(
            stdout_lines=[
                json.dumps(
                    {
                        "auth_strategy": "device_code",
                        "provider": "GitHub",
                        "initial_permissions": ["repo"],
                    }
                )
            ]
        )

        async def _fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return fake_process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

        manager = SilasConnectionManager(skills_dir=skills_dir)
        result = await manager.discover_connection("github", {"email": "dev@example.com"})

        assert result["provider"] == "GitHub"
        assert fake_process.communicated_input is not None
        parsed_input = json.loads(fake_process.communicated_input.decode("utf-8").strip())
        assert parsed_input["identity_hint"]["email"] == "dev@example.com"

    @pytest.mark.asyncio
    async def test_run_setup_flow_streams_steps_and_collects_responses(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        skills_dir = tmp_path / "skills"
        _touch_script(skills_dir, "github", "setup.py")
        fake_process = _FakeProcess(
            stdout_lines=[
                json.dumps(
                    {
                        "type": "setup_step",
                        "step": {
                            "type": "secure_input",
                            "request": {"ref_id": "ref-1", "label": "GitHub PAT"},
                        },
                    }
                ),
                json.dumps({"type": "await_input", "step_id": "step-1"}),
                json.dumps(
                    {
                        "type": "setup_step",
                        "step": {
                            "type": "completion",
                            "success": True,
                            "summary": "Connected",
                            "permissions_granted": ["repo"],
                        },
                    }
                ),
            ]
        )

        async def _fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return fake_process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

        manager = SilasConnectionManager(skills_dir=skills_dir)
        steps = await manager.run_setup_flow(
            "github",
            {"email": "dev@example.com"},
            responses=[SetupStepResponse(step_type="secure_input", action="done")],
        )

        assert [step.type for step in steps] == ["secure_input", "completion"]
        writes = [json.loads(chunk.decode("utf-8").strip()) for chunk in fake_process.stdin.writes]
        assert writes[0]["type"] == "start"
        assert writes[1]["type"] == "step_result"
        assert writes[1]["payload"]["action"] == "done"

    @pytest.mark.asyncio
    async def test_activate_and_list_connections(self, tmp_path: Path) -> None:
        manager = SilasConnectionManager(skills_dir=tmp_path / "skills")
        first_id = await manager.activate_connection(
            skill_name="github",
            provider="GitHub",
            auth_payload={
                "permissions_granted": ["repo"],
                "domain": "engineering",
            },
        )
        await manager.activate_connection(
            skill_name="notion",
            provider="Notion",
            auth_payload={"permissions_granted": ["read"]},
        )

        all_connections = await manager.list_connections()
        engineering = await manager.list_connections("engineering")

        assert len(all_connections) == 2
        assert len(engineering) == 1
        assert engineering[0].connection_id == first_id

    @pytest.mark.asyncio
    async def test_health_check_updates_active_connections(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        skills_dir = tmp_path / "skills"
        _touch_script(skills_dir, "github", "health_check.py")

        manager = SilasConnectionManager(skills_dir=skills_dir)
        connection_id = await manager.activate_connection(
            skill_name="github",
            provider="GitHub",
            auth_payload={},
        )

        expires_at = (_now() + timedelta(minutes=5)).isoformat()
        fake_process = _FakeProcess(
            stdout_lines=[
                json.dumps(
                    {
                        "healthy": True,
                        "token_expires_at": expires_at,
                        "refresh_token_expires_at": None,
                        "latency_ms": 47,
                        "warnings": [],
                    }
                )
            ]
        )

        async def _fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return fake_process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

        results = await manager.run_health_checks()
        connections = await manager.list_connections()

        assert len(results) == 1
        assert results[0].healthy is True
        assert connections[0].connection_id == connection_id
        assert connections[0].last_health_check is not None
        assert connection_id in manager.scheduled_refreshes

    @pytest.mark.asyncio
    async def test_refresh_token_updates_record(self, tmp_path: Path, monkeypatch) -> None:
        skills_dir = tmp_path / "skills"
        _touch_script(skills_dir, "github", "refresh_token.py")
        manager = SilasConnectionManager(skills_dir=skills_dir)
        connection_id = await manager.activate_connection(
            skill_name="github",
            provider="GitHub",
            auth_payload={},
        )

        new_expiry = (_now() + timedelta(hours=2)).isoformat()
        fake_process = _FakeProcess(
            stdout_lines=[json.dumps({"success": True, "new_expires_at": new_expiry})]
        )

        async def _fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return fake_process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

        refreshed = await manager.refresh_token(connection_id)
        connection = (await manager.list_connections())[0]

        assert refreshed is True
        assert connection.last_refresh is not None
        assert connection.status == "active"
        assert connection.token_expires_at is not None

    @pytest.mark.asyncio
    async def test_recover_returns_success_and_message(self, tmp_path: Path, monkeypatch) -> None:
        skills_dir = tmp_path / "skills"
        _touch_script(skills_dir, "github", "recover.py")
        manager = SilasConnectionManager(skills_dir=skills_dir)
        connection_id = await manager.activate_connection(
            skill_name="github",
            provider="GitHub",
            auth_payload={"status": "error"},
        )

        fake_process = _FakeProcess(stdout_lines=[json.dumps({"success": True, "message": "Recovered"})])

        async def _fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return fake_process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

        success, message = await manager.recover(connection_id)
        connection = (await manager.list_connections())[0]

        assert success is True
        assert message == "Recovered"
        assert connection.status == "active"

    @pytest.mark.asyncio
    async def test_connection_status_transitions_on_failures(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        skills_dir = tmp_path / "skills"
        _touch_script(skills_dir, "github", "health_check.py")
        _touch_script(skills_dir, "github", "recover.py")
        manager = SilasConnectionManager(skills_dir=skills_dir)
        connection_id = await manager.activate_connection(
            skill_name="github",
            provider="GitHub",
            auth_payload={},
        )

        async def _fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN002, ANN003
            script_path = str(args[1])
            del kwargs
            if script_path.endswith("health_check.py"):
                return _FakeProcess(
                    stdout_lines=[
                        json.dumps(
                            {
                                "healthy": False,
                                "latency_ms": 0,
                                "error": "token revoked",
                                "warnings": ["reauth required"],
                            }
                        )
                    ]
                )
            return _FakeProcess(
                stdout_lines=[
                    json.dumps(
                        {
                            "failure_type": "token_revoked",
                            "service": "GitHub",
                            "message": "Sign in again",
                        }
                    )
                ]
            )

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

        await manager.run_health_checks()
        after_health = (await manager.list_connections())[0]
        success, message = await manager.recover(connection_id)
        after_recover = (await manager.list_connections())[0]

        assert after_health.status == "error"
        assert success is False
        assert "Sign in again" in message
        assert after_recover.status == "error"

    @pytest.mark.asyncio
    async def test_empty_connections_list(self, tmp_path: Path) -> None:
        manager = SilasConnectionManager(skills_dir=tmp_path / "skills")
        assert await manager.list_connections() == []


class TestSQLiteConnectionStore:
    @pytest.mark.asyncio
    async def test_sqlite_connection_store_crud(self, tmp_path: Path) -> None:
        db_path = tmp_path / "connections.db"
        await run_migrations(str(db_path))
        store = SQLiteConnectionStore(str(db_path))

        now = _now()
        connection = Connection(
            connection_id="conn-1",
            skill_name="github",
            provider="GitHub",
            status="active",
            permissions_granted=["repo"],
            created_at=now,
            updated_at=now,
        )
        await store.save_connection(connection)

        loaded = await store.get_connection("conn-1")
        listed = await store.list_connections()
        filtered = await store.list_connections("git")
        deleted = await store.delete_connection("conn-1")
        missing = await store.get_connection("conn-1")

        assert loaded is not None
        assert loaded.provider == "GitHub"
        assert len(listed) == 1
        assert len(filtered) == 1
        assert deleted is True
        assert missing is None
