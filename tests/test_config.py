"""Tests for configuration loading and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from silas.config import StreamConfig, WebChannelConfig


class TestWebChannelConfig:
    def test_localhost_no_auth_allowed(self) -> None:
        cfg = WebChannelConfig(host="127.0.0.1", auth_token=None)
        assert cfg.auth_token is None

    def test_0000_requires_auth_token(self) -> None:
        with pytest.raises(ValidationError, match="auth_token is required"):
            WebChannelConfig(host="0.0.0.0", auth_token=None)

    def test_0000_with_auth_allowed(self) -> None:
        cfg = WebChannelConfig(host="0.0.0.0", auth_token="secret123")
        assert cfg.auth_token == "secret123"


class TestStreamConfig:
    def test_streaming_defaults(self) -> None:
        cfg = StreamConfig()
        assert cfg.streaming_enabled is True
        assert cfg.chunk_size == 50

    def test_chunk_size_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            StreamConfig(chunk_size=0)
