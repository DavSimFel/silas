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
        # Input: agent_name, owner_name, api_key, passphrase, passphrase_confirm
        result = runner.invoke(
            cli,
            ["init", "--config", str(config_file)],
            input="TestBot\nAlice\nsk-fake-key\ntestpass\ntestpass\n",
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
            input="Bot\nBob\nbad-key\ngood-key\ntestpass\ntestpass\n",
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


def test_secrets_endpoint(web_channel, web_config_file: Path) -> None:
    """POST /secrets/{ref_id} stores secret in the configured data directory."""
    from fastapi.testclient import TestClient

    client = TestClient(web_channel.app)
    resp = client.post("/secrets/my-ref", json={"value": "my-secret"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ref_id"] == "my-ref"
    assert data["success"] is True
    loaded = yaml.safe_load(web_config_file.read_text())
    data_dir = Path(loaded["silas"]["data_dir"])
    assert SecretStore(data_dir).get("my-ref") == "my-secret"


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


# ---------------------------------------------------------------------------
# Tier 2 — SigningKeyStore tests
# ---------------------------------------------------------------------------


def test_signing_key_store_roundtrip(tmp_path: Path) -> None:
    """Generate, store, and reload Ed25519 keypair with passphrase."""
    from silas.secrets import SigningKeyStore

    store = SigningKeyStore(tmp_path, "test-passphrase")
    assert not store.has_keypair()

    pub_hex = store.generate_keypair()
    assert len(pub_hex) == 64  # 32 bytes hex
    assert store.has_keypair()

    pk = store.load_private_key()
    # Verify the loaded key can sign
    sig = pk.sign(b"hello")
    pk.public_key().verify(sig, b"hello")  # no exception = valid


def test_signing_key_store_wrong_passphrase(tmp_path: Path) -> None:
    """Wrong passphrase cannot read back the key."""
    from silas.secrets import SigningKeyStore

    store1 = SigningKeyStore(tmp_path, "correct-passphrase")
    store1.generate_keypair()

    store2 = SigningKeyStore(tmp_path, "wrong-passphrase")
    assert not store2.has_keypair()  # can't decrypt → no key found


def test_signing_key_store_persists(tmp_path: Path) -> None:
    """Same passphrase across instances recovers the key."""
    from silas.secrets import SigningKeyStore

    store1 = SigningKeyStore(tmp_path, "my-pass")
    pub1 = store1.generate_keypair()

    store2 = SigningKeyStore(tmp_path, "my-pass")
    pub2 = store2.get_public_key_hex()
    assert pub1 == pub2


def test_signing_key_store_encrypted_on_disk(tmp_path: Path) -> None:
    """Signing key file must not contain plaintext key material."""
    from silas.secrets import SigningKeyStore

    store = SigningKeyStore(tmp_path, "p")
    store.generate_keypair()

    enc_file = tmp_path / ".signing.enc"
    assert enc_file.exists()
    raw = enc_file.read_bytes()
    # The Ed25519 private key is 32 bytes — shouldn't appear in plaintext
    assert b"ed25519" not in raw.lower()


@pytest.mark.anyio
async def test_signing_key_integrated_with_verifier(tmp_path: Path) -> None:
    """Tier 2 key works with SilasApprovalVerifier end-to-end."""
    from silas.approval.verifier import SilasApprovalVerifier
    from silas.models.approval import ApprovalDecision, ApprovalVerdict
    from silas.models.work import WorkItem
    from silas.secrets import SigningKeyStore

    # In-memory nonce store
    class _Nonces:
        def __init__(self) -> None:
            self._k: set[str] = set()

        async def is_used(self, d: str, n: str) -> bool:
            return f"{d}:{n}" in self._k

        async def record(self, d: str, n: str) -> None:
            self._k.add(f"{d}:{n}")

        async def prune_expired(self, older_than: object) -> int:
            return 0

    store = SigningKeyStore(tmp_path, "secure")
    store.generate_keypair()
    pk = store.load_private_key()

    verifier = SilasApprovalVerifier(signing_key=pk, nonce_store=_Nonces())
    wi = WorkItem(id="t1", type="task", title="Test", body="body")
    decision = ApprovalDecision(verdict=ApprovalVerdict.approved)

    token = await verifier.issue_token(wi, decision)
    valid, reason = await verifier.verify(token, wi)
    assert valid is True
    assert reason == "ok"
