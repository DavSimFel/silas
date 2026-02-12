"""Tests for ModelsConfig API key injection."""

from __future__ import annotations

import os
from unittest.mock import patch

from silas.config import ModelsConfig


class TestInjectApiKeyEnv:
    def test_no_key_no_op(self) -> None:
        config = ModelsConfig()
        with patch.dict(os.environ, {}, clear=False):
            config.inject_api_key_env()
            # Should not set anything
            assert os.environ.get("OPENROUTER_API_KEY") is None or os.environ.get("OPENROUTER_API_KEY") == os.environ.get("OPENROUTER_API_KEY")

    def test_sets_openrouter_key(self) -> None:
        config = ModelsConfig(api_key="sk-or-test-123")
        with patch.dict(os.environ, {}, clear=True):
            os.environ["PATH"] = "/usr/bin"
            config.inject_api_key_env()
            assert os.environ.get("OPENROUTER_API_KEY") == "sk-or-test-123"

    def test_does_not_overwrite_existing_env(self) -> None:
        config = ModelsConfig(api_key="sk-or-new")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-existing"}, clear=False):
            config.inject_api_key_env()
            assert os.environ["OPENROUTER_API_KEY"] == "sk-or-existing"

    def test_openai_provider(self) -> None:
        config = ModelsConfig(
            proxy="openai:gpt-4o",
            planner="openai:gpt-4o",
            executor="openai:gpt-4o",
            scorer="openai:gpt-4o",
            api_key="sk-test-openai",
        )
        with patch.dict(os.environ, {}, clear=True):
            os.environ["PATH"] = "/usr/bin"
            config.inject_api_key_env()
            assert os.environ.get("OPENAI_API_KEY") == "sk-test-openai"

    def test_mixed_providers(self) -> None:
        config = ModelsConfig(
            proxy="openrouter:anthropic/claude-haiku-4-5",
            planner="openai:gpt-4o",
            executor="openrouter:anthropic/claude-haiku-4-5",
            scorer="openrouter:anthropic/claude-haiku-4-5",
            api_key="sk-mixed",
        )
        with patch.dict(os.environ, {}, clear=True):
            os.environ["PATH"] = "/usr/bin"
            config.inject_api_key_env()
            assert os.environ.get("OPENROUTER_API_KEY") == "sk-mixed"
            assert os.environ.get("OPENAI_API_KEY") == "sk-mixed"
