"""Silas solver for Inspect AI — connects to a running Silas instance via WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from inspect_ai.solver import Generate, TaskState, solver

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:8420"
_DEFAULT_WS_TIMEOUT = 30.0


async def _check_health(base_url: str) -> dict[str, Any]:
    """Hit GET /health and return the parsed JSON."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(f"{base_url}/health")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


def _build_ws_url(
    base_url: str,
    auth_token: str | None,
    session: str | None,
) -> str:
    """Build a WebSocket URL from HTTP base URL and query parameters."""
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    params: list[str] = []
    if auth_token:
        params.append(f"token={auth_token}")
    if session:
        params.append(f"session={session}")
    if params:
        ws_url += "?" + "&".join(params)
    return ws_url


async def _collect_ws_response(
    ws: Any,
    timeout: float,
    result: dict[str, Any],
) -> None:
    """Read WebSocket messages until a terminal event or timeout."""
    collected_text = ""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1))
        except TimeoutError:
            break

        parsed = json.loads(raw)
        result["raw_messages"].append(parsed)
        msg_type = parsed.get("type", "")

        if msg_type == "stream_chunk":
            collected_text += parsed.get("text", "")
        elif msg_type == "stream_end":
            break
        elif msg_type == "message":
            collected_text += parsed.get("text", "")
            break
        elif msg_type in ("approval_request", "gate_approval"):
            collected_text = json.dumps(parsed)
            break

    result["response_text"] = collected_text


async def _ws_roundtrip(
    base_url: str,
    message: str,
    *,
    auth_token: str | None = None,
    session: str | None = None,
    timeout: float = _DEFAULT_WS_TIMEOUT,
    expect_rejection: bool = False,
) -> dict[str, Any]:
    """Send a message over WebSocket and collect the response.

    Returns a dict with keys: connected, rejected, response_text, raw_messages.
    """
    try:
        import websockets
    except ModuleNotFoundError:
        raise ImportError(  # noqa: B904
            "websockets package required for Silas solver — install uvicorn[standard]"
        )

    ws_url = _build_ws_url(base_url, auth_token, session)
    result: dict[str, Any] = {
        "connected": False,
        "rejected": False,
        "response_text": "",
        "raw_messages": [],
    }

    try:
        async with websockets.connect(ws_url, open_timeout=timeout) as ws:
            result["connected"] = True
            if expect_rejection:
                result["rejected"] = False
                await ws.close()
                return result

            await ws.send(json.dumps({"type": "message", "text": message}))
            await _collect_ws_response(ws, timeout, result)

    except Exception as exc:
        exc_str = str(exc)
        if "4401" in exc_str or "unauthorized" in exc_str.lower():
            result["rejected"] = True
        else:
            logger.debug("WebSocket connection error: %s", exc)
            result["error"] = exc_str

    return result


@solver
def silas_health_check(base_url: str = _DEFAULT_BASE_URL):
    """Solver that checks the /health endpoint and stores the result."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        try:
            health = await _check_health(base_url)
            state.output = state.output.model_copy(
                update={"completion": json.dumps(health)},
            )
        except Exception as exc:
            state.output = state.output.model_copy(
                update={"completion": f"HEALTH_CHECK_FAILED: {exc}"},
            )
        return state

    return solve


@solver
def silas_ws_auth_check(
    base_url: str = _DEFAULT_BASE_URL,
    auth_token: str | None = None,
):
    """Solver that tests WebSocket auth enforcement."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        # Extract test parameters from the sample input
        user_msg = state.messages[-1].text if state.messages else ""

        if "without_token" in user_msg or "no_token" in user_msg:
            result = await _ws_roundtrip(base_url, "", auth_token=None, expect_rejection=True)
        else:
            result = await _ws_roundtrip(base_url, user_msg, auth_token=auth_token)

        state.output = state.output.model_copy(
            update={"completion": json.dumps(result)},
        )
        return state

    return solve


@solver
def silas_message(
    base_url: str = _DEFAULT_BASE_URL,
    auth_token: str | None = None,
    timeout: float = _DEFAULT_WS_TIMEOUT,
):
    """Solver that sends a user message to Silas and collects the response."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        user_msg = state.messages[-1].text if state.messages else ""
        result = await _ws_roundtrip(
            base_url,
            user_msg,
            auth_token=auth_token,
            timeout=timeout,
        )
        completion = result.get("response_text", "")
        if not completion and result.get("error"):
            completion = f"ERROR: {result['error']}"
        state.output = state.output.model_copy(
            update={"completion": completion},
        )
        state.metadata["ws_result"] = result
        return state

    return solve


@solver
def silas_http_request(
    base_url: str = _DEFAULT_BASE_URL,
    method: str = "GET",
    path: str = "/health",
):
    """Solver that makes an HTTP request to a Silas endpoint."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        url = f"{base_url}{path}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            try:
                resp = await client.request(method, url)
                result = {
                    "status_code": resp.status_code,
                    "body": resp.text,
                }
            except Exception as exc:
                result = {"error": str(exc)}

        state.output = state.output.model_copy(
            update={"completion": json.dumps(result)},
        )
        return state

    return solve


__all__ = [
    "silas_health_check",
    "silas_http_request",
    "silas_message",
    "silas_ws_auth_check",
]
