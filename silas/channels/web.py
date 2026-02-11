from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from silas.models.messages import ChannelMessage, utc_now
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
        self._websocket: WebSocket | None = None
        self._ws_lock = asyncio.Lock()
        self.app = FastAPI(title="Silas WebChannel")
        self._setup_routes()

    def _setup_routes(self) -> None:
        @self.app.get("/health")
        async def health() -> JSONResponse:
            connected = 1 if self._websocket is not None else 0
            return JSONResponse({"status": "ok", "connections": connected})

        @self.app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket) -> None:
            await websocket.accept()
            async with self._ws_lock:
                self._websocket = websocket

            try:
                while True:
                    payload = await websocket.receive_text()
                    await self._handle_client_payload(payload)
            except WebSocketDisconnect:
                pass
            finally:
                async with self._ws_lock:
                    if self._websocket is websocket:
                        self._websocket = None

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

    async def _handle_client_payload(self, payload: str) -> None:
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
            timestamp=utc_now(),
        )
        await self._incoming.put((message, self.scope_id))

    async def listen(self) -> AsyncIterator[tuple[ChannelMessage, str]]:
        while True:
            yield await self._incoming.get()

    async def send(self, recipient_id: str, text: str, reply_to: str | None = None) -> None:
        del recipient_id
        async with self._ws_lock:
            websocket = self._websocket
        if websocket is None:
            return

        payload = {
            "type": "message",
            "sender": "silas",
            "text": text,
            "reply_to": reply_to,
            "timestamp": utc_now().isoformat(),
        }
        await websocket.send_text(json.dumps(payload))

    async def serve(self, log_level: str = "info") -> None:
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level=log_level)
        server = uvicorn.Server(config)
        await server.serve()


__all__ = ["WebChannel"]
