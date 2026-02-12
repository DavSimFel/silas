from __future__ import annotations

import asyncio
import inspect
import json
import logging
import mimetypes
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, field_validator

from silas.models.approval import ApprovalVerdict
from silas.models.messages import ChannelMessage, utc_now
from silas.protocols.channels import ChannelAdapterCore

try:  # pragma: no cover - optional dependency
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover - optional dependency
    WebPushException = Exception
    webpush = None


type ApprovalResponseHandler = Callable[[str, ApprovalVerdict, str], Awaitable[None] | None]

logger = logging.getLogger(__name__)


class _SecretPayload(BaseModel):
    value: str


class OnboardPayload(BaseModel):
    agent_name: str
    api_key: str
    owner_name: str

    @field_validator("agent_name", "api_key", "owner_name")
    @classmethod
    def _validate_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned


class WebChannel(ChannelAdapterCore):
    channel_name = "web"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8420,
        web_dir: str | Path = "web",
        scope_id: str = "owner",
        auth_token: str | None = None,
        config_path: str | Path = "config/silas.yaml",
    ) -> None:
        self.host = host
        self.port = port
        self.scope_id = scope_id
        self._auth_token = auth_token
        self.web_dir = Path(web_dir)
        self._config_path = Path(config_path)
        self._incoming: asyncio.Queue[tuple[ChannelMessage, str]] = asyncio.Queue()
        self._websocket: WebSocket | None = None
        self._websockets_by_session: dict[str, WebSocket] = {}
        self._active_sessions_by_connection: dict[str, set[str]] = {}
        self._approval_response_handler: ApprovalResponseHandler | None = None
        self._ws_lock = asyncio.Lock()

        self._push_subscriptions: dict[str, dict[str, object]] = {}
        self._vapid_private_key = os.getenv("SILAS_VAPID_PRIVATE_KEY", "")
        self._vapid_subject = os.getenv("SILAS_VAPID_SUBJECT", "mailto:silas@localhost")

        self.app = FastAPI(title="Silas WebChannel")
        self._setup_routes()

    def _setup_routes(self) -> None:
        @self.app.get("/health")
        async def health() -> JSONResponse:
            connected = len(self._websockets_by_session)
            return JSONResponse(
                {
                    "status": "ok",
                    "connections": connected,
                    "sessions": sorted(self._websockets_by_session.keys()),
                },
            )

        self._setup_push_routes()
        self._setup_secret_routes()
        self._setup_onboarding_routes()

        @self.app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket) -> None:
            if not await self._check_ws_auth(websocket):
                return

            session_id = self._resolve_session_id(websocket.query_params.get("session"))
            connection_key = self._connection_key(websocket)

            await websocket.accept()
            await self._register_websocket(session_id, connection_key, websocket)

            try:
                while True:
                    payload = await websocket.receive_text()
                    await self._handle_client_payload(payload, session_id=session_id)
            except WebSocketDisconnect:
                pass
            finally:
                await self._unregister_websocket(session_id, connection_key, websocket)

        if self.web_dir.exists():

            @self.app.get("/")
            async def index() -> Response:
                return self._serve_static("index.html")

            @self.app.get("/{asset_path:path}")
            async def static_asset(asset_path: str) -> Response:
                return self._serve_static(asset_path)

    async def _register_websocket(
        self, session_id: str, connection_key: str, websocket: WebSocket,
    ) -> None:
        """Track a new websocket connection in session and connection maps."""
        async with self._ws_lock:
            self._websockets_by_session[session_id] = websocket
            self._active_sessions_by_connection.setdefault(connection_key, set()).add(session_id)
            if session_id == self.scope_id or self._websocket is None:
                self._websocket = websocket

    async def _unregister_websocket(
        self, session_id: str, connection_key: str, websocket: WebSocket,
    ) -> None:
        """Clean up websocket tracking on disconnect, promoting next available socket."""
        async with self._ws_lock:
            current = self._websockets_by_session.get(session_id)
            if current is websocket:
                self._websockets_by_session.pop(session_id, None)

            sessions = self._active_sessions_by_connection.get(connection_key)
            if sessions is not None:
                sessions.discard(session_id)
                if not sessions:
                    self._active_sessions_by_connection.pop(connection_key, None)

            if self._websocket is websocket:
                self._websocket = self._websockets_by_session.get(self.scope_id)
                if self._websocket is None and self._websockets_by_session:
                    self._websocket = next(iter(self._websockets_by_session.values()))

    def _serve_static(self, asset_path: str) -> Response:
        asset = asset_path.lstrip("/")
        target = (self.web_dir / asset).resolve()
        web_root = self.web_dir.resolve()

        # Block path traversal and reject unknown files.
        if web_root not in target.parents:
            raise HTTPException(status_code=404)
        if not target.is_file():
            raise HTTPException(status_code=404)

        media_type, _ = mimetypes.guess_type(str(target))
        headers = {}
        # Prevent caching of service worker and HTML
        if asset in ("sw.js", "index.html", ""):
            headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return Response(
            content=target.read_bytes(),
            media_type=media_type or "application/octet-stream",
            headers=headers,
        )

    def _setup_push_routes(self) -> None:
        @self.app.post("/api/push/subscribe")
        async def push_subscribe(payload: dict[str, object]) -> JSONResponse:
            endpoint = self._extract_subscription_endpoint(payload)
            if endpoint is None:
                raise HTTPException(status_code=400, detail="Invalid push subscription payload")
            self._push_subscriptions[endpoint] = payload
            return JSONResponse({"status": "ok", "count": len(self._push_subscriptions)})

        @self.app.post("/api/push/unsubscribe")
        async def push_unsubscribe(payload: dict[str, object]) -> JSONResponse:
            endpoint = self._extract_subscription_endpoint(payload)
            if endpoint is None:
                raise HTTPException(status_code=400, detail="Invalid push subscription payload")
            removed = self._push_subscriptions.pop(endpoint, None) is not None
            return JSONResponse(
                {
                    "status": "ok",
                    "removed": removed,
                    "count": len(self._push_subscriptions),
                },
            )

    def _setup_secret_routes(self) -> None:
        """POST /secrets/{ref_id} — secure credential ingestion (§0.5).

        Bypasses WebSocket so secrets never enter the agent pipeline.
        """

        @self.app.post("/secrets/{ref_id}")
        async def store_secret(ref_id: str, request: _SecretPayload) -> JSONResponse:
            from silas.secrets import SecretStore

            data_dir = Path("./data")  # TODO: wire from settings
            store = SecretStore(data_dir)
            store.set(ref_id, request.value)
            return JSONResponse({"ref_id": ref_id, "success": True})

    def _setup_onboarding_routes(self) -> None:
        @self.app.post("/api/onboard")
        async def onboard(payload: OnboardPayload) -> JSONResponse:
            is_valid_key = await self._validate_openrouter_key(payload.api_key)
            if not is_valid_key:
                raise HTTPException(status_code=400, detail="Invalid OpenRouter API key")

            try:
                self._write_onboarding_config(payload)
            except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
                logger.warning("Failed to persist onboarding config", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail="Unable to persist onboarding settings",
                ) from exc

            return JSONResponse({"status": "ok"})

    async def _validate_openrouter_key(self, api_key: str) -> bool:
        headers = {"Authorization": f"Bearer {api_key}"}
        timeout = httpx.Timeout(8.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
        except httpx.HTTPError:
            logger.warning("OpenRouter key validation request failed", exc_info=True)
            return False
        return response.status_code == 200

    def _write_onboarding_config(self, payload: OnboardPayload) -> None:
        config_data = self._load_config_mapping()
        silas_section = config_data.get("silas")
        if silas_section is None:
            silas_section = {}
            config_data["silas"] = silas_section
        if not isinstance(silas_section, dict):
            raise ValueError("config top-level 'silas' key must be a mapping")

        models_section = silas_section.get("models")
        if models_section is None:
            models_section = {}
            silas_section["models"] = models_section
        if not isinstance(models_section, dict):
            raise ValueError("config 'silas.models' key must be a mapping")

        silas_section["agent_name"] = payload.agent_name
        silas_section["owner_name"] = payload.owner_name

        # Store API key in SecretStore (§0.5 — never in config files)
        from silas.secrets import SecretStore

        data_dir = Path(silas_section.get("data_dir", "./data"))
        api_key_ref = "openrouter-api-key"
        secret_store = SecretStore(data_dir)
        secret_store.set(api_key_ref, payload.api_key)
        models_section["api_key_ref"] = api_key_ref

        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = yaml.safe_dump(config_data, sort_keys=False)
        self._config_path.write_text(rendered, encoding="utf-8")

    def _load_config_mapping(self) -> dict[str, object]:
        if not self._config_path.exists():
            return {}

        loaded = yaml.safe_load(self._config_path.read_text(encoding="utf-8"))
        if loaded is None:
            return {}
        if not isinstance(loaded, dict):
            raise ValueError("config file must contain a top-level mapping")
        return loaded

    async def _check_ws_auth(self, websocket: WebSocket) -> bool:
        """Reject WebSocket connections that fail token auth. Returns True if authorized."""
        if not self._auth_token:
            return True
        token = websocket.query_params.get("token", "")
        if token != self._auth_token:
            await websocket.close(code=4401, reason="unauthorized")
            return False
        return True

    def _resolve_session_id(self, raw_session: str | None) -> str:
        if raw_session is None:
            return self.scope_id
        cleaned = raw_session.strip()
        return cleaned or self.scope_id

    @staticmethod
    def _connection_key(websocket: WebSocket) -> str:
        client = websocket.client
        if client is None:
            return f"unknown:{id(websocket)}"
        return f"{client.host}:{client.port}"

    def _extract_subscription_endpoint(self, payload: dict[str, object]) -> str | None:
        endpoint = payload.get("endpoint")
        if isinstance(endpoint, str) and endpoint.strip():
            return endpoint.strip()

        subscription = payload.get("subscription")
        if isinstance(subscription, dict):
            nested_endpoint = subscription.get("endpoint")
            if isinstance(nested_endpoint, str) and nested_endpoint.strip():
                return nested_endpoint.strip()
        return None

    def active_sessions(self) -> dict[str, list[str]]:
        return {
            connection: sorted(sessions)
            for connection, sessions in self._active_sessions_by_connection.items()
        }

    async def notify_subscribers(
        self,
        title: str,
        body: str,
        data: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        if not self._push_subscriptions:
            return {"sent": 0, "failed": 0}

        if webpush is None:
            return {
                "sent": 0,
                "failed": len(self._push_subscriptions),
                "reason": "pywebpush_unavailable",
            }

        if not self._vapid_private_key:
            return {
                "sent": 0,
                "failed": len(self._push_subscriptions),
                "reason": "missing_vapid_private_key",
            }

        payload = json.dumps(
            {
                "title": title,
                "body": body,
                "data": data or {},
            },
        )

        sent = 0
        failed = 0
        stale_endpoints: list[str] = []
        for endpoint, subscription in list(self._push_subscriptions.items()):
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info=subscription,
                    data=payload,
                    vapid_private_key=self._vapid_private_key,
                    vapid_claims={"sub": self._vapid_subject},
                )
                sent += 1
            except WebPushException as exc:  # pragma: no cover - depends on pywebpush runtime
                failed += 1
                response = getattr(exc, "response", None)
                status_code = getattr(response, "status_code", None)
                if status_code in {404, 410}:
                    stale_endpoints.append(endpoint)
            except (OSError, ValueError, RuntimeError):
                logger.warning("Push notification failed for endpoint", exc_info=True)
                failed += 1

        for endpoint in stale_endpoints:
            self._push_subscriptions.pop(endpoint, None)

        return {
            "sent": sent,
            "failed": failed,
            "remaining": len(self._push_subscriptions),
        }

    async def _handle_client_payload(self, payload: str, session_id: str) -> None:
        sender_id = self.scope_id
        text = payload
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                msg_type = str(parsed.get("type", "message"))
                if msg_type == "approval_response":
                    await self._handle_approval_response(parsed)
                    return
                if msg_type != "message":
                    return
                text = str(parsed.get("text", ""))
                # sender_id is server-assigned, never from client payload.
                # Authenticated WebSocket connections are the owner.
        except json.JSONDecodeError:
            pass

        message = ChannelMessage(
            channel=self.channel_name,
            sender_id=sender_id,
            text=text,
            timestamp=utc_now(),
        )
        await self._incoming.put((message, session_id))

    async def listen(self) -> AsyncIterator[tuple[ChannelMessage, str]]:
        while True:
            yield await self._incoming.get()

    async def _resolve_socket_for_recipient(self, recipient_id: str) -> WebSocket | None:
        async with self._ws_lock:
            if recipient_id and recipient_id in self._websockets_by_session:
                return self._websockets_by_session[recipient_id]
            if self.scope_id in self._websockets_by_session:
                return self._websockets_by_session[self.scope_id]
            if self._websocket is not None:
                return self._websocket
            if self._websockets_by_session:
                return next(iter(self._websockets_by_session.values()))
            return None

    async def send(self, recipient_id: str, text: str, reply_to: str | None = None) -> None:
        payload = {
            "type": "message",
            "sender": "silas",
            "text": text,
            "reply_to": reply_to,
            "timestamp": utc_now().isoformat(),
        }
        await self._send_json(recipient_id, payload)

    async def send_stream_start(self, connection_id: str) -> None:
        payload = {
            "type": "stream_start",
            "timestamp": utc_now().isoformat(),
        }
        await self._send_json(connection_id, payload)

    async def send_stream_chunk(self, connection_id: str, text: str) -> None:
        payload = {
            "type": "stream_chunk",
            "text": text,
            "timestamp": utc_now().isoformat(),
        }
        await self._send_json(connection_id, payload)

    async def send_stream_end(self, connection_id: str) -> None:
        payload = {
            "type": "stream_end",
            "timestamp": utc_now().isoformat(),
        }
        await self._send_json(connection_id, payload)

    async def send_approval_card(self, recipient_id: str, card: dict[str, object]) -> None:
        payload = {
            "type": "approval_card",
            "card": card,
            "timestamp": utc_now().isoformat(),
        }
        await self._send_json(recipient_id, payload)

    async def _send_json(self, recipient_id: str, payload: dict[str, object]) -> None:
        websocket = await self._resolve_socket_for_recipient(recipient_id)
        if websocket is None:
            return
        await websocket.send_text(json.dumps(payload))

    def register_approval_response_handler(self, handler: ApprovalResponseHandler | None) -> None:
        self._approval_response_handler = handler

    async def _handle_approval_response(self, payload: dict[str, object]) -> None:
        card_id = payload.get("card_id")
        action = payload.get("action")
        if not isinstance(card_id, str) or not card_id.strip():
            return
        if action not in {"approve", "decline", "deny", "defer"}:
            return
        if action == "defer":
            return

        normalized_action = "decline" if action == "deny" else action
        handler = self._approval_response_handler
        if handler is None:
            return

        # resolved_by is always the authenticated owner — never from client payload.
        resolved_by = self.scope_id
        verdict = (
            ApprovalVerdict.approved
            if normalized_action == "approve"
            else ApprovalVerdict.declined
        )
        result = handler(card_id, verdict, resolved_by)
        if inspect.isawaitable(result):
            await result

    async def serve(self, log_level: str = "info") -> None:
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level=log_level)
        server = uvicorn.Server(config)
        await server.serve()


__all__ = ["WebChannel"]
