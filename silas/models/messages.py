from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class TaintLevel(StrEnum):
    owner = "owner"
    auth = "auth"
    external = "external"


class ChannelMessage(BaseModel):
    channel: str
    sender_id: str
    text: str
    timestamp: datetime = Field(default_factory=utc_now)
    attachments: list[str] = Field(default_factory=list)
    reply_to: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class SignedMessage(BaseModel):
    message: ChannelMessage
    signature: bytes
    nonce: str
    taint: TaintLevel = TaintLevel.external


def signed_message_canonical_bytes(message: ChannelMessage, nonce: str) -> bytes:
    payload = {
        "text": message.text,
        "timestamp": message.timestamp.isoformat(),
        "nonce": nonce,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


__all__ = [
    "ChannelMessage",
    "SignedMessage",
    "TaintLevel",
    "signed_message_canonical_bytes",
    "utc_now",
]
