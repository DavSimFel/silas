"""Regression tests for recently merged security fixes."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from silas.channels.web import WebChannel
from silas.connections.manager import SilasConnectionManager
from silas.core.plan_executor import plan_action_to_work_item
from silas.core.plan_parser import MarkdownPlanParser
from silas.goals.manager import SilasGoalManager
from silas.models.approval import ApprovalVerdict

_PLAN_MARKDOWN_NEEDS_APPROVAL_FALSE = """---
id: wi-markdown
type: task
title: Markdown work item
needs_approval: false
---

Execute markdown plan item.
"""


def _action_from_plan_markdown() -> dict[str, object]:
    return {"plan_markdown": _PLAN_MARKDOWN_NEEDS_APPROVAL_FALSE}


def _action_from_explicit_work_item() -> dict[str, object]:
    return {
        "work_item": {
            "id": "wi-explicit",
            "type": "task",
            "title": "Explicit work item",
            "body": "Execute explicit payload.",
            "needs_approval": False,
        }
    }


def _action_from_inline_payload() -> dict[str, object]:
    return {
        "id": "wi-inline",
        "type": "task",
        "title": "Inline work item",
        "body": "Execute inline payload.",
        "needs_approval": False,
    }


@pytest.mark.parametrize("path", ["/ws", "/ws?token=wrong-token"])
def test_ws_rejects_without_valid_token_when_auth_token_configured(path: str) -> None:
    channel = WebChannel(auth_token="expected-token")
    with TestClient(channel.app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(path):
                pass

    assert exc_info.value.code == 4401


def test_ws_accepts_with_valid_token_when_auth_token_configured() -> None:
    channel = WebChannel(auth_token="expected-token")
    with TestClient(channel.app) as client:
        with client.websocket_connect("/ws?token=expected-token") as websocket:
            websocket.send_text("hello")


def test_ws_loopback_accepts_when_no_auth_token_configured() -> None:
    channel = WebChannel(auth_token=None)
    with TestClient(channel.app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_text("hello")


@pytest.mark.asyncio
async def test_client_sender_id_is_ignored_and_server_assigns_scope_id() -> None:
    channel = WebChannel(scope_id="owner-scope")

    payload: str = json.dumps(
        {
            "type": "message",
            "text": "hello from attacker",
            "sender_id": "attacker",
        }
    )
    await channel._handle_client_payload(payload, session_id="session-1")
    message, session_id = await channel._incoming.get()

    assert session_id == "session-1"
    assert message.sender_id == "owner-scope"
    assert message.text == "hello from attacker"


@pytest.mark.asyncio
async def test_approval_response_resolved_by_is_server_scope_id() -> None:
    channel = WebChannel(scope_id="owner-scope")
    captured: list[tuple[str, ApprovalVerdict, str]] = []

    async def _handler(card_id: str, verdict: ApprovalVerdict, resolved_by: str) -> None:
        captured.append((card_id, verdict, resolved_by))

    channel.register_approval_response_handler(_handler)

    await channel._handle_approval_response(
        {
            "type": "approval_response",
            "card_id": "card-1",
            "action": "approve",
            "sender_id": "attacker",
        }
    )

    assert captured == [("card-1", ApprovalVerdict.approved, "owner-scope")]


@pytest.mark.parametrize(
    "action_factory",
    [
        _action_from_plan_markdown,
        _action_from_explicit_work_item,
        _action_from_inline_payload,
    ],
)
def test_plan_action_to_work_item_always_forces_needs_approval(
    action_factory: Callable[[], dict[str, object]],
) -> None:
    parser = MarkdownPlanParser()
    action = action_factory()

    work_item = plan_action_to_work_item(
        action,
        parser=parser,
        index=0,
        turn_number=7,
    )

    assert work_item.needs_approval is True


@pytest.mark.asyncio
async def test_goal_manager_run_awaitable_logs_background_task_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = SilasGoalManager(goals_config=[], work_item_store=object())

    async def _failing_save() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("persistence write failed")

    goal_logger = logging.getLogger("silas.goals.manager")
    goal_logger.propagate = True
    with caplog.at_level(logging.ERROR):
        manager._run_awaitable(_failing_save())
        # Let the event loop process the task and its done-callback.
        await asyncio.sleep(0.1)

    matching = [r for r in caplog.records if "Background save failed" in r.getMessage()]
    assert matching, "Expected 'Background save failed' log entry"


def test_connection_manager_resolve_script_rejects_traversal_skill_name(tmp_path: Path) -> None:
    manager = SilasConnectionManager(skills_dir=tmp_path / "skills")

    with pytest.raises(ValueError, match="skill name escapes skills directory"):
        manager._resolve_script("../etc", "discover.py")


def test_connection_manager_resolve_script_rejects_traversal_script_name(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "safe-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "discover.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    manager = SilasConnectionManager(skills_dir=skills_dir)

    with pytest.raises(ValueError, match="script path escapes skills directory"):
        manager._resolve_script("safe-skill", "../../etc/passwd")
