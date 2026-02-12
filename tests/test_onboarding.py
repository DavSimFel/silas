"""Tests for onboarding — CLI (main.py) and web endpoint (web.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from click.testing import CliRunner
from silas.main import cli

# ---------------------------------------------------------------------------
# CLI onboarding tests
# ---------------------------------------------------------------------------


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Create a minimal silas.yaml with default owner_id."""
    cfg = tmp_path / "silas.yaml"
    cfg.write_text(
        yaml.safe_dump({"silas": {"owner_id": "owner", "data_dir": str(tmp_path / "data")}}, sort_keys=False),
        encoding="utf-8",
    )
    return cfg


def test_init_skips_when_already_configured(tmp_path: Path) -> None:
    """If owner_id != 'owner', onboarding is skipped."""
    cfg = tmp_path / "silas.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"silas": {"owner_id": "real-user", "agent_name": "Pal", "data_dir": str(tmp_path / "data")}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--config", str(cfg)], input="\n")
    assert "Already configured" in result.output


def test_init_writes_config(config_file: Path) -> None:
    """Onboarding prompts write agent_name, owner_name, and api_key to YAML."""
    runner = CliRunner()
    with patch("silas.main._validate_openrouter_api_key", return_value=True):
        result = runner.invoke(
            cli,
            ["init", "--config", str(config_file)],
            input="TestBot\nAlice\nsk-fake-key\n",
        )

    assert result.exit_code == 0, result.output
    loaded = yaml.safe_load(config_file.read_text())
    assert loaded["silas"]["agent_name"] == "TestBot"
    assert loaded["silas"]["owner_name"] == "Alice"
    assert loaded["silas"]["models"]["api_key"] == "sk-fake-key"


def test_init_retries_invalid_key(config_file: Path) -> None:
    """Invalid API key triggers retry prompt."""
    runner = CliRunner()
    with patch("silas.main._validate_openrouter_api_key", side_effect=[False, True]):
        result = runner.invoke(
            cli,
            ["init", "--config", str(config_file)],
            input="Bot\nBob\nbad-key\ngood-key\n",
        )

    assert result.exit_code == 0, result.output
    assert "Invalid OpenRouter API key" in result.output
    loaded = yaml.safe_load(config_file.read_text())
    assert loaded["silas"]["models"]["api_key"] == "good-key"


# ---------------------------------------------------------------------------
# Web onboarding endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def web_config_file(tmp_path: Path) -> Path:
    cfg = tmp_path / "silas.yaml"
    cfg.write_text(
        yaml.safe_dump({"silas": {"owner_id": "owner"}}, sort_keys=False),
        encoding="utf-8",
    )
    return cfg


@pytest.fixture
def web_channel(web_config_file: Path):
    from silas.channels.web import WebChannel

    return WebChannel(host="127.0.0.1", port=0, config_path=web_config_file)


@pytest.mark.anyio
async def test_onboard_endpoint_success(web_channel, web_config_file: Path) -> None:
    from fastapi.testclient import TestClient

    with patch.object(web_channel, "_validate_openrouter_key", new_callable=AsyncMock, return_value=True):
        client = TestClient(web_channel.app)
        resp = client.post(
            "/api/onboard",
            json={"agent_name": "Pal", "api_key": "sk-test", "owner_name": "Tester"},
        )

    assert resp.status_code == 200
    loaded = yaml.safe_load(web_config_file.read_text())
    assert loaded["silas"]["agent_name"] == "Pal"
    assert loaded["silas"]["models"]["api_key"] == "sk-test"


@pytest.mark.anyio
async def test_onboard_endpoint_invalid_key(web_channel) -> None:
    from fastapi.testclient import TestClient

    with patch.object(web_channel, "_validate_openrouter_key", new_callable=AsyncMock, return_value=False):
        client = TestClient(web_channel.app)
        resp = client.post(
            "/api/onboard",
            json={"agent_name": "Pal", "api_key": "bad", "owner_name": "Tester"},
        )

    assert resp.status_code == 400
    assert "Invalid" in resp.json()["detail"]


@pytest.mark.anyio
async def test_onboard_endpoint_blank_name(web_channel) -> None:
    from fastapi.testclient import TestClient

    client = TestClient(web_channel.app)
    resp = client.post(
        "/api/onboard",
        json={"agent_name": "  ", "api_key": "sk-x", "owner_name": "Tester"},
    )
    assert resp.status_code == 422  # Pydantic validation
