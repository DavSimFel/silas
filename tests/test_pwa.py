"""PWA asset serving and service worker tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from silas.channels.web import WebChannel


@pytest.fixture
def web_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "web"


@pytest.fixture
async def client(web_dir: Path) -> AsyncClient:
    channel = WebChannel(web_dir=web_dir)
    transport = ASGITransport(app=channel.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestStaticAssets:
    """Verify all PWA-critical files are served correctly."""

    async def test_index_html_served(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_index_has_manifest_link(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert 'rel="manifest"' in resp.text
        assert 'href="/manifest.json"' in resp.text

    async def test_index_has_theme_color(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert 'name="theme-color"' in resp.text

    async def test_index_has_apple_meta(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert "apple-mobile-web-app-capable" in resp.text
        assert "apple-touch-icon" in resp.text

    async def test_index_has_connection_status(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert 'id="connection-status"' in resp.text

    async def test_index_has_install_button(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert 'id="install-btn"' in resp.text


class TestManifest:
    """Validate manifest.json structure per PWA spec."""

    async def test_manifest_served(self, client: AsyncClient) -> None:
        resp = await client.get("/manifest.json")
        assert resp.status_code == 200

    async def test_manifest_valid_json(self, client: AsyncClient) -> None:
        resp = await client.get("/manifest.json")
        data = resp.json()
        assert isinstance(data, dict)

    async def test_manifest_required_fields(self, client: AsyncClient) -> None:
        data = (await client.get("/manifest.json")).json()
        assert data["name"] == "Silas"
        assert data["short_name"] == "Silas"
        assert data["start_url"] == "/"
        assert data["display"] == "standalone"

    async def test_manifest_has_colors(self, client: AsyncClient) -> None:
        data = (await client.get("/manifest.json")).json()
        assert "background_color" in data
        assert "theme_color" in data

    async def test_manifest_icons_present(self, client: AsyncClient) -> None:
        data = (await client.get("/manifest.json")).json()
        icons = data["icons"]
        assert len(icons) >= 2
        sizes = {icon["sizes"] for icon in icons}
        assert "192x192" in sizes
        assert "512x512" in sizes

    async def test_manifest_has_maskable_icon(self, client: AsyncClient) -> None:
        data = (await client.get("/manifest.json")).json()
        maskable = [i for i in data["icons"] if i.get("purpose") == "maskable"]
        assert len(maskable) >= 1


class TestServiceWorker:
    """Validate service worker is served correctly."""

    async def test_sw_served(self, client: AsyncClient) -> None:
        resp = await client.get("/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]

    async def test_sw_has_cache_name(self, client: AsyncClient) -> None:
        resp = await client.get("/sw.js")
        assert "CACHE_NAME" in resp.text

    async def test_sw_caches_shell(self, client: AsyncClient) -> None:
        resp = await client.get("/sw.js")
        for asset in ["/", "/style.css", "/app.js", "/manifest.json"]:
            assert asset in resp.text, f"SW should cache {asset}"

    async def test_sw_skips_websocket(self, client: AsyncClient) -> None:
        resp = await client.get("/sw.js")
        assert "/ws" in resp.text  # skip logic present

    async def test_sw_has_install_handler(self, client: AsyncClient) -> None:
        resp = await client.get("/sw.js")
        assert "install" in resp.text

    async def test_sw_has_activate_handler(self, client: AsyncClient) -> None:
        resp = await client.get("/sw.js")
        assert "activate" in resp.text

    async def test_sw_has_fetch_handler(self, client: AsyncClient) -> None:
        resp = await client.get("/sw.js")
        assert "fetch" in resp.text


class TestIcons:
    """Validate icon files are served."""

    async def test_icon_192(self, client: AsyncClient) -> None:
        resp = await client.get("/icons/icon-192.png")
        assert resp.status_code == 200
        assert "image/png" in resp.headers["content-type"]

    async def test_icon_512(self, client: AsyncClient) -> None:
        resp = await client.get("/icons/icon-512.png")
        assert resp.status_code == 200

    async def test_icon_maskable(self, client: AsyncClient) -> None:
        resp = await client.get("/icons/icon-512-maskable.png")
        assert resp.status_code == 200


class TestAppJs:
    """Validate app.js has critical features."""

    async def test_app_js_served(self, client: AsyncClient) -> None:
        resp = await client.get("/app.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]

    async def test_app_has_sw_registration(self, client: AsyncClient) -> None:
        resp = await client.get("/app.js")
        assert "serviceWorker" in resp.text
        assert "register" in resp.text

    async def test_app_has_install_prompt(self, client: AsyncClient) -> None:
        resp = await client.get("/app.js")
        assert "beforeinstallprompt" in resp.text

    async def test_app_has_reconnect_logic(self, client: AsyncClient) -> None:
        resp = await client.get("/app.js")
        assert "scheduleReconnect" in resp.text
        assert "reconnectAttempt" in resp.text

    async def test_app_has_exponential_backoff(self, client: AsyncClient) -> None:
        resp = await client.get("/app.js")
        assert "RECONNECT_BASE_MS" in resp.text
        assert "RECONNECT_MAX_MS" in resp.text
        assert "Math.pow" in resp.text

    async def test_app_has_offline_handling(self, client: AsyncClient) -> None:
        resp = await client.get("/app.js")
        assert '"online"' in resp.text
        assert '"offline"' in resp.text

    async def test_app_has_connection_status(self, client: AsyncClient) -> None:
        resp = await client.get("/app.js")
        assert "setStatus" in resp.text
        assert "connected" in resp.text
        assert "offline" in resp.text
        assert "connecting" in resp.text


class TestStyleCss:
    """Validate CSS has connection status styles."""

    async def test_css_served(self, client: AsyncClient) -> None:
        resp = await client.get("/style.css")
        assert resp.status_code == 200

    async def test_css_has_connection_status_styles(self, client: AsyncClient) -> None:
        resp = await client.get("/style.css")
        assert ".connection-status" in resp.text
        assert "offline" in resp.text
        assert "connecting" in resp.text
