"""Tests for onboarding — CLI (main.py) and web endpoint (web.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from click.testing import CliRunner
from silas.main import cli
from silas.secrets import SecretStore

# ---------------------------------------------------------------------------
# CLI onboarding tests
# ---------------------------------------------------------------------------


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Create a minimal silas.yaml with default owner_id."""
    cfg = tmp_path / "silas.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"silas": {"owner_id": "owner", "data_dir": str(tmp_path / "data")}},
            sort_keys=False,
        ),
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


def test_init_writes_config_and_secret(config_file: Path, tmp_path: Path) -> None:
    """Onboarding writes agent_name/owner_name to YAML and API key to SecretStore."""
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
    # API key must NOT be in YAML — only a ref_id
    assert "api_key" not in loaded["silas"].get("models", {})
    assert loaded["silas"]["models"]["api_key_ref"] == "openrouter-api-key"

    # Verify the key is in SecretStore
    data_dir = Path(loaded["silas"]["data_dir"])
    store = SecretStore(data_dir)
    assert store.get("openrouter-api-key") == "sk-fake-key"


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
    assert loaded["silas"]["models"]["api_key_ref"] == "openrouter-api-key"


# ---------------------------------------------------------------------------
# Web onboarding endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def web_config_file(tmp_path: Path) -> Path:
    cfg = tmp_path / "silas.yaml"
    cfg.write_text(
        yaml.safe_dump({"silas": {"owner_id": "owner", "data_dir": str(tmp_path / "data")}}, sort_keys=False),
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
    # API key must NOT be in YAML
    assert "api_key" not in loaded["silas"].get("models", {})
    assert loaded["silas"]["models"]["api_key_ref"] == "openrouter-api-key"


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


# ---------------------------------------------------------------------------
# SecretStore tests
# ---------------------------------------------------------------------------


def test_secret_store_roundtrip(tmp_path: Path) -> None:
    """Set, get, delete cycle works with encrypted file backend."""
    store = SecretStore(tmp_path)
    assert store.get("test-key") is None

    store.set("test-key", "secret-value")
    assert store.get("test-key") == "secret-value"

    store.delete("test-key")
    assert store.get("test-key") is None


def test_secret_store_encrypted_file_exists(tmp_path: Path) -> None:
    """Encrypted file is created on first write."""
    store = SecretStore(tmp_path)
    store.set("k", "v")
    enc_file = tmp_path / ".secrets.enc"
    assert enc_file.exists()
    # File content must not contain the plaintext value
    raw = enc_file.read_bytes()
    assert b"secret-value" not in raw
    assert b'"v"' not in raw  # also not the JSON representation


def test_secret_store_persists_across_instances(tmp_path: Path) -> None:
    """A new SecretStore instance reads secrets from the same file."""
    store1 = SecretStore(tmp_path)
    store1.set("persistent", "stays")

    store2 = SecretStore(tmp_path)
    assert store2.get("persistent") == "stays"


def test_secrets_endpoint(web_channel) -> None:
    """POST /secrets/{ref_id} stores secret in SecretStore."""
    from fastapi.testclient import TestClient

    client = TestClient(web_channel.app)
    resp = client.post("/secrets/my-ref", json={"value": "my-secret"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ref_id"] == "my-ref"
    assert data["success"] is True


def test_api_key_ref_resolution(tmp_path: Path) -> None:
    """ModelsConfig.resolve_api_key() resolves ref_id from SecretStore."""
    from silas.config import ModelsConfig

    store = SecretStore(tmp_path)
    store.set("openrouter-api-key", "sk-resolved")

    config = ModelsConfig(api_key_ref="openrouter-api-key")
    assert config.resolve_api_key(data_dir=tmp_path) == "sk-resolved"


def test_api_key_ref_fallback_to_direct(tmp_path: Path) -> None:
    """When ref_id not found, falls back to direct api_key."""
    from silas.config import ModelsConfig

    config = ModelsConfig(api_key="sk-direct", api_key_ref="missing-ref")
    assert config.resolve_api_key(data_dir=tmp_path) == "sk-direct"
