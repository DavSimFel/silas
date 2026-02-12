"""Tests for WebChannel RichCardChannel methods."""

from __future__ import annotations

import asyncio
import json

import pytest
from silas.channels.web import WebChannel
from silas.models.approval import ApprovalVerdict
from silas.models.review import DecisionResult


@pytest.fixture
def channel(tmp_path) -> WebChannel:
    return WebChannel(
        host="127.0.0.1",
        port=0,
        config_path=tmp_path / "silas.yaml",
    )


def _simulate_card_response(
    channel: WebChannel,
    response_data: dict,
    delay: float = 0.01,
) -> None:
    """Schedule a card_response to arrive after a short delay."""

    async def _respond() -> None:
        await asyncio.sleep(delay)
        # Find the pending card_id and resolve it
        for card_id, future in list(channel._pending_card_responses.items()):
            if not future.done():
                future.set_result({"card_id": card_id, **response_data})
                break

    asyncio.get_event_loop().create_task(_respond())


@pytest.mark.anyio
async def test_send_approval_request() -> None:
    from silas.models.work import WorkItem

    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    wi = WorkItem(id="wi-1", title="Test plan", body="Do stuff", type="task")

    _simulate_card_response(ch, {"verdict": "approved"})
    decision = await ch.send_approval_request("owner", wi)

    assert decision.verdict == ApprovalVerdict.approved


@pytest.mark.anyio
async def test_send_card_and_wait_timeout() -> None:
    """Verify the timeout path returns a timed_out dict and cleans up."""
    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    result = await ch._send_card_and_wait("owner", "test_card", {}, timeout=0.01)
    assert result.get("timed_out") is True
    assert len(ch._pending_card_responses) == 0


@pytest.mark.anyio
async def test_send_gate_approval() -> None:
    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    _simulate_card_response(ch, {"action": "approve"})
    result = await ch.send_gate_approval("owner", "risk_gate", 0.85, "high risk transaction")
    assert result == "approve"


@pytest.mark.anyio
async def test_send_gate_approval_defaults_to_block() -> None:
    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    _simulate_card_response(ch, {"action": "invalid_action"})
    result = await ch.send_gate_approval("owner", "gate", "value", "ctx")
    assert result == "block"


@pytest.mark.anyio
async def test_send_decision() -> None:
    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    _simulate_card_response(ch, {"selected_value": "option_a", "approved": True})
    result = await ch.send_decision("owner", "Pick one", [], allow_freetext=False)
    assert isinstance(result, DecisionResult)
    assert result.selected_value == "option_a"
    assert result.approved is True


@pytest.mark.anyio
async def test_send_draft_review() -> None:
    from silas.models.draft import DraftVerdict

    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    _simulate_card_response(ch, {"verdict": "approve"})
    result = await ch.send_draft_review("owner", "context", "draft text", {})
    assert result == DraftVerdict.approve


@pytest.mark.anyio
async def test_send_secure_input() -> None:
    from silas.models.connections import SecureInputCompleted, SecureInputRequest

    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    req = SecureInputRequest(ref_id="ref-123", label="API Token", guidance={})
    _simulate_card_response(ch, {"success": True})
    result = await ch.send_secure_input("owner", req)
    assert isinstance(result, SecureInputCompleted)
    assert result.ref_id == "ref-123"
    assert result.success is True


@pytest.mark.anyio
async def test_send_connection_setup_step() -> None:
    from silas.models.connections import SetupStep, SetupStepResponse

    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    step = SetupStep(type="device_code", verification_url="https://example.com", user_code="ABC123")
    _simulate_card_response(ch, {"step_type": "device_code", "action": "done"})
    result = await ch.send_connection_setup_step("owner", step)
    assert isinstance(result, SetupStepResponse)
    assert result.action == "done"


@pytest.mark.anyio
async def test_send_permission_escalation() -> None:
    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    _simulate_card_response(ch, {"approved": True, "selected_value": "granted"})
    result = await ch.send_permission_escalation(
        "owner", "outlook", ["read"], ["read", "write"], "need write access",
    )
    assert isinstance(result, DecisionResult)
    assert result.approved is True


@pytest.mark.anyio
async def test_send_connection_failure() -> None:
    from silas.models.connections import ConnectionFailure

    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    failure = ConnectionFailure(
        failure_type="token_revoked",
        service="Microsoft 365",
        message="Token was revoked",
        recovery_options=[],
    )
    _simulate_card_response(ch, {"selected_value": "retry", "approved": False})
    result = await ch.send_connection_failure("owner", failure)
    assert isinstance(result, DecisionResult)
    assert result.selected_value == "retry"


@pytest.mark.anyio
async def test_resolve_card_response_ignores_unknown_id() -> None:
    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")
    # Should not raise
    ch._resolve_card_response({"card_id": "nonexistent", "data": "whatever"})
    ch._resolve_card_response({"no_card_id": True})


@pytest.mark.anyio
async def test_card_response_via_handle_client_payload() -> None:
    """card_response messages are routed to _resolve_card_response."""
    ch = WebChannel(host="127.0.0.1", port=0, config_path="/tmp/test.yaml")

    future: asyncio.Future = asyncio.get_event_loop().create_future()
    ch._pending_card_responses["test-card-id"] = future

    payload = json.dumps({
        "type": "card_response",
        "card_id": "test-card-id",
        "verdict": "approved",
    })
    await ch._handle_client_payload(payload, session_id="main")

    assert future.done()
    assert future.result()["verdict"] == "approved"
