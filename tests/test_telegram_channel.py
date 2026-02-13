"""Tests for the Telegram channel adapter."""

from __future__ import annotations

import httpx
import pytest
from silas.channels.telegram import TelegramChannel, TelegramConfig, _split_text

# ── Fixtures ─────────────────────────────────────────────────────────


def _make_channel(
    owner_ids: list[str] | None = None,
    transport: httpx.MockTransport | None = None,
) -> TelegramChannel:
    config = TelegramConfig(
        bot_token="123:FAKE",
        owner_chat_ids=owner_ids or ["111"],
    )
    client = httpx.AsyncClient(transport=transport or httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})))
    return TelegramChannel(config=config, http_client=client)


def _update(chat_id: int = 111, text: str = "hello", reply_to_msg_id: int | None = None) -> dict:
    msg: dict = {
        "message_id": 42,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "is_bot": False, "first_name": "Test"},
        "text": text,
        "date": 1700000000,
    }
    if reply_to_msg_id is not None:
        msg["reply_to_message"] = {"message_id": reply_to_msg_id}
    return {"update_id": 1, "message": msg}


# ── Webhook → ChannelMessage ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_update_to_channel_message() -> None:
    ch = _make_channel()
    await ch.handle_update(_update(chat_id=111, text="ping"))

    msg, session = ch._incoming.get_nowait()
    assert msg.channel == "telegram"
    assert msg.sender_id == "111"
    assert msg.text == "ping"
    assert session == "owner"


@pytest.mark.asyncio
async def test_unknown_chat_id_not_owner() -> None:
    ch = _make_channel(owner_ids=["111"])
    await ch.handle_update(_update(chat_id=999, text="hi"))

    msg, session = ch._incoming.get_nowait()
    assert msg.sender_id == "999"
    assert session == "tg:999"


@pytest.mark.asyncio
async def test_reply_to_threading() -> None:
    ch = _make_channel()
    await ch.handle_update(_update(reply_to_msg_id=7))

    msg, _ = ch._incoming.get_nowait()
    assert msg.reply_to == "7"


@pytest.mark.asyncio
async def test_non_text_update_ignored() -> None:
    ch = _make_channel()
    # Photo message with no text field
    await ch.handle_update({"update_id": 1, "message": {"message_id": 1, "chat": {"id": 111}, "photo": []}})
    assert ch._incoming.empty()


@pytest.mark.asyncio
async def test_no_message_key_ignored() -> None:
    ch = _make_channel()
    await ch.handle_update({"update_id": 1, "edited_message": {"text": "edited"}})
    assert ch._incoming.empty()


# ── Message splitting ────────────────────────────────────────────────


def test_short_message_no_split() -> None:
    assert _split_text("hello") == ["hello"]


def test_long_message_split_on_newline() -> None:
    line = "x" * 2000
    text = f"{line}\n{line}\n{line}"
    chunks = _split_text(text, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    # Reassembled content should match (modulo stripped newlines at boundaries)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_long_message_hard_split() -> None:
    # Single line longer than limit — must hard-split
    text = "a" * 10000
    chunks = _split_text(text, limit=4096)
    assert len(chunks) == 3
    assert chunks[0] == "a" * 4096
    assert chunks[1] == "a" * 4096
    assert chunks[2] == "a" * 1808


# ── send_message ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message_calls_api() -> None:
    requests_made: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_made.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    ch = _make_channel(transport=httpx.MockTransport(handler))
    await ch.send("111", "hello world", reply_to="5")

    assert len(requests_made) == 1
    import json
    body = json.loads(requests_made[0].content)
    assert body["chat_id"] == "111"
    assert body["text"] == "hello world"
    assert body["parse_mode"] == "Markdown"
    assert body["reply_parameters"] == {"message_id": 5}


@pytest.mark.asyncio
async def test_send_long_message_splits() -> None:
    requests_made: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_made.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    ch = _make_channel(transport=httpx.MockTransport(handler))
    await ch.send("111", "a" * 5000)

    # Should split into 2 chunks
    assert len(requests_made) == 2


@pytest.mark.asyncio
async def test_send_reply_to_only_first_chunk() -> None:
    """Only the first chunk should carry reply_to; rest flow naturally."""
    import json
    requests_made: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_made.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    ch = _make_channel(transport=httpx.MockTransport(handler))
    await ch.send("111", "a" * 5000, reply_to="10")

    body_0 = json.loads(requests_made[0].content)
    body_1 = json.loads(requests_made[1].content)
    assert "reply_parameters" in body_0
    assert "reply_parameters" not in body_1


# ── channel_name property ───────────────────────────────────────────


def test_channel_name() -> None:
    ch = _make_channel()
    assert ch.channel_name == "telegram"
