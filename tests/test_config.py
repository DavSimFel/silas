"""Tests for configuration loading and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from silas.config import WebChannelConfig


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
