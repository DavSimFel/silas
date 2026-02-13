"""Telegram Bot API channel adapter.

Uses raw HTTP (httpx) instead of python-telegram-bot to keep the dependency
footprint minimal — we only need sendMessage + webhook ingestion.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from silas.models.messages import ChannelMessage, utc_now

logger = logging.getLogger(__name__)

# Telegram enforces a hard 4096 UTF-8 character limit per message.
_TG_MAX_LEN = 4096

_API_BASE = "https://api.telegram.org"


@dataclass
class TelegramConfig:
    bot_token: str
    owner_chat_ids: list[str] = field(default_factory=list)
    webhook_path: str = "/telegram/webhook"


def _split_text(text: str, limit: int = _TG_MAX_LEN) -> list[str]:
    """Split long text into chunks that fit Telegram's message limit.

    Tries to break on newlines to preserve readability; falls back to
    hard splits when a single line exceeds the limit.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        # Prefer splitting at the last newline within the limit
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    return chunks


class TelegramChannel:
    """Telegram channel adapter implementing ChannelAdapterCore.

    Receives updates via webhook (FastAPI route) and sends responses
    via the Bot API HTTP endpoint.
    """

    channel_name = "telegram"

    def __init__(
        self,
        config: TelegramConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        # Allow injection for testing — avoids real HTTP in tests
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        self._incoming: asyncio.Queue[tuple[ChannelMessage, str]] = asyncio.Queue()
        self._owner_ids = set(config.owner_chat_ids)

    @property
    def api_url(self) -> str:
        return f"{_API_BASE}/bot{self._config.bot_token}"

    # ── Inbound (webhook) ────────────────────────────────────────────

    async def handle_update(self, update: dict[str, object]) -> None:
        """Parse a raw Telegram Update dict and enqueue as ChannelMessage.

        Called by the webhook route handler. Silently drops updates that
        don't contain a text message (edits, media, etc.) — those can be
        added later without changing the interface.
        """
        message = update.get("message")
        if not isinstance(message, dict):
            return

        text = message.get("text")
        if not isinstance(text, str):
            return

        chat = message.get("chat", {})
        if not isinstance(chat, dict):
            return
        chat_id = str(chat.get("id", ""))
        if not chat_id:
            return

        reply_to_message_id: str | None = None
        reply = message.get("reply_to_message")
        if isinstance(reply, dict):
            mid = reply.get("message_id")
            if mid is not None:
                reply_to_message_id = str(mid)

        is_owner = chat_id in self._owner_ids

        channel_msg = ChannelMessage(
            channel=self.channel_name,
            sender_id=chat_id,
            text=text,
            timestamp=utc_now(),
            reply_to=reply_to_message_id,
        )

        # Session key doubles as taint signal — owner chat_ids get "owner"
        # scope so the trust pipeline can elevate their messages.
        session_id = "owner" if is_owner else f"tg:{chat_id}"
        await self._incoming.put((channel_msg, session_id))

    # ── ChannelAdapterCore ───────────────────────────────────────────

    async def listen(self) -> AsyncIterator[tuple[ChannelMessage, str]]:
        while True:
            yield await self._incoming.get()

    async def send(
        self,
        recipient_id: str,
        text: str,
        reply_to: str | None = None,
    ) -> None:
        """Send a message (possibly multi-part) to a Telegram chat."""
        chunks = _split_text(text)
        for chunk in chunks:
            await self._send_message(recipient_id, chunk, reply_to=reply_to)
            # Only thread the first chunk; subsequent chunks flow naturally
            reply_to = None

    async def send_stream_start(self, connection_id: str) -> None:
        # Telegram has no streaming primitive — no-op by design.
        pass

    async def send_stream_chunk(self, connection_id: str, text: str) -> None:
        # Could buffer and edit-in-place later; for now, no-op.
        pass

    async def send_stream_end(self, connection_id: str) -> None:
        pass

    # ── Bot API calls ────────────────────────────────────────────────

    async def _send_message(
        self,
        chat_id: str,
        text: str,
        reply_to: str | None = None,
    ) -> dict[str, object]:
        """POST sendMessage to the Telegram Bot API."""
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        if reply_to is not None:
            payload["reply_parameters"] = {"message_id": int(reply_to)}

        url = f"{self.api_url}/sendMessage"
        resp = await self._http.post(url, json=payload)
        resp.raise_for_status()
        result = resp.json()
        if not isinstance(result, dict):
            return {}
        return result

    # ── FastAPI route factory ────────────────────────────────────────

    def register_routes(self, app: object) -> None:
        """Attach the webhook POST route to a FastAPI app.

        Kept as explicit registration rather than owning the app so Telegram
        can coexist with WebChannel on the same server.
        """
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse

        assert isinstance(app, FastAPI)

        @app.post(self._config.webhook_path)
        async def telegram_webhook(request: Request) -> JSONResponse:
            body = await request.json()
            await self.handle_update(body)
            # Telegram expects 200 OK quickly; async processing happens via queue
            return JSONResponse({"ok": True})


__all__ = ["TelegramChannel", "TelegramConfig"]
