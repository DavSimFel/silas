from __future__ import annotations

import asyncio
import json
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from silas.models.messages import ChannelMessage
from silas.protocols.channels import ChannelAdapterCore


class WebChannel(ChannelAdapterCore):
    channel_name = "web"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8420,
        web_dir: str | Path = "web",
        scope_id: str = "owner",
    ) -> None:
        self.host = host
        self.port = port
        self.scope_id = scope_id
        self.web_dir = Path(web_dir)
        self._incoming: asyncio.Queue[tuple[ChannelMessage, str]] = asyncio.Queue()
        self._connections: dict[str, WebSocket] = {}
        self._active_streams: set[str] = set()
        self._ws_lock = asyncio.Lock()
        self.app = FastAPI(title="Silas WebChannel")
        self._setup_routes()

    def _setup_routes(self) -> None:
        @self.app.get("/health")
        async def health() -> JSONResponse:
            async with self._ws_lock:
                connected = len(self._connections)
                active_streams = len(self._active_streams)
            return JSONResponse(
                {"status": "ok", "connections": connected, "active_streams": active_streams}
            )

        @self.app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket) -> None:
            connection_id = uuid.uuid4().hex
            await websocket.accept()
            async with self._ws_lock:
                self._connections[connection_id] = websocket

            try:
                while True:
                    payload = await websocket.receive_text()
                    await self._handle_client_payload(payload, connection_id)
            except WebSocketDisconnect:
                pass
            finally:
                await self._cleanup_connection(connection_id, websocket)

        if self.web_dir.exists():

            @self.app.get("/")
            async def index() -> Response:
                return self._serve_static("index.html")

            @self.app.get("/{asset_path:path}")
            async def static_asset(asset_path: str) -> Response:
                return self._serve_static(asset_path)

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
        return Response(
            content=target.read_bytes(),
            media_type=media_type or "application/octet-stream",
        )

    async def _handle_client_payload(self, payload: str, connection_id: str) -> None:
        sender_id = self.scope_id
        text = payload
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                msg_type = str(parsed.get("type", "message"))
                if msg_type != "message":
                    return
                text = str(parsed.get("text", ""))
                sender_id = str(parsed.get("sender_id", self.scope_id))
        except json.JSONDecodeError:
            pass

        message = ChannelMessage(
            channel=self.channel_name,
            sender_id=sender_id,
            text=text,
            timestamp=datetime.now(timezone.utc),
        )
        await self._incoming.put((message, connection_id))

    async def listen(self) -> AsyncIterator[tuple[ChannelMessage, str]]:
        while True:
            yield await self._incoming.get()

    async def send(self, recipient_id: str, text: str, reply_to: str | None = None) -> None:
        payload = {
            "type": "message",
            "sender": "silas",
            "text": text,
            "reply_to": reply_to,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._send_payload(recipient_id, payload)

    async def send_stream_start(self, connection_id: str) -> None:
        resolved_id = await self._resolve_connection_id(connection_id)
        if resolved_id is None:
            return
        async with self._ws_lock:
            self._active_streams.add(resolved_id)
        await self._send_payload(resolved_id, {"type": "stream_start"})

    async def send_stream_chunk(self, connection_id: str, text: str) -> None:
        if not text:
            return
        resolved_id = await self._resolve_connection_id(connection_id)
        if resolved_id is None:
            return
        await self._send_payload(resolved_id, {"type": "stream_chunk", "text": text})

    async def send_stream_end(self, connection_id: str) -> None:
        resolved_id = await self._resolve_connection_id(connection_id)
        if resolved_id is None:
            return
        try:
            await self._send_payload(resolved_id, {"type": "stream_end"})
        finally:
            async with self._ws_lock:
                self._active_streams.discard(resolved_id)

    async def _send_payload(self, recipient_id: str, payload: dict[str, object]) -> None:
        async with self._ws_lock:
            resolved_id, websocket = self._resolve_connection_locked(recipient_id)
        if websocket is None or resolved_id is None:
            return

        try:
            await websocket.send_text(json.dumps(payload))
        except (RuntimeError, WebSocketDisconnect):
            await self._cleanup_connection(resolved_id, websocket)

    async def _resolve_connection_id(self, recipient_id: str) -> str | None:
        async with self._ws_lock:
            resolved_id, _ = self._resolve_connection_locked(recipient_id)
        return resolved_id

    def _resolve_connection_locked(self, recipient_id: str) -> tuple[str | None, WebSocket | None]:
        websocket = self._connections.get(recipient_id)
        if websocket is not None:
            return recipient_id, websocket

        if recipient_id == self.scope_id and self._connections:
            fallback_id = next(iter(self._connections))
            return fallback_id, self._connections[fallback_id]

        return None, None

    async def _cleanup_connection(
        self,
        connection_id: str,
        websocket: WebSocket | None = None,
    ) -> None:
        async with self._ws_lock:
            current = self._connections.get(connection_id)
            if current is None:
                self._active_streams.discard(connection_id)
                return

            if websocket is not None and current is not websocket:
                return

            self._connections.pop(connection_id, None)
            self._active_streams.discard(connection_id)

    async def serve(self, log_level: str = "info") -> None:
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level=log_level)
        server = uvicorn.Server(config)
        await server.serve()


__all__ = ["WebChannel"]
