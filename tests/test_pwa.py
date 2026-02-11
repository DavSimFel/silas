"""PWA asset serving and frontend tests — Quiet design."""

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


# ── Static Assets ──


class TestStaticAssets:
    async def test_index_served(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_index_has_manifest_link(self, client: AsyncClient) -> None:
        assert 'rel="manifest"' in (await client.get("/")).text

    async def test_index_has_theme_color(self, client: AsyncClient) -> None:
        assert 'name="theme-color"' in (await client.get("/")).text

    async def test_index_has_apple_meta(self, client: AsyncClient) -> None:
        html = (await client.get("/")).text
        assert "apple-mobile-web-app-capable" in html
        assert "apple-touch-icon" in html

    async def test_index_has_viewport_fit_cover(self, client: AsyncClient) -> None:
        assert "viewport-fit=cover" in (await client.get("/")).text


# ── Quiet Design Structure ──


class TestQuietStructure:
    """Verify Quiet design language elements in HTML."""

    async def test_status_strip_present(self, client: AsyncClient) -> None:
        assert 'id="status-strip"' in (await client.get("/")).text

    async def test_status_dot_present(self, client: AsyncClient) -> None:
        assert 'id="status-dot"' in (await client.get("/")).text

    async def test_stream_present(self, client: AsyncClient) -> None:
        assert 'id="stream"' in (await client.get("/")).text

    async def test_empty_state_present(self, client: AsyncClient) -> None:
        html = (await client.get("/")).text
        assert 'id="empty-state"' in html
        assert "🪶" in html
        assert "What should I work on?" in html

    async def test_composer_is_textarea(self, client: AsyncClient) -> None:
        html = (await client.get("/")).text
        assert "<textarea" in html
        assert 'id="message-input"' in html

    async def test_send_button_present(self, client: AsyncClient) -> None:
        assert 'id="send-btn"' in (await client.get("/")).text

    async def test_install_button_present(self, client: AsyncClient) -> None:
        assert 'id="install-btn"' in (await client.get("/")).text

    async def test_no_chat_bubbles(self, client: AsyncClient) -> None:
        """Quiet design: no chat bubble classes in markup."""
        html = (await client.get("/")).text
        assert "msg-you" not in html
        assert "msg-silas" not in html
        assert "chat-bubble" not in html

    async def test_max_width_constraint(self, client: AsyncClient) -> None:
        """Stream content constrained to 760px per spec."""
        assert "760px" in (await client.get("/")).text


# ── Manifest ──


class TestManifest:
    async def test_manifest_served(self, client: AsyncClient) -> None:
        assert (await client.get("/manifest.json")).status_code == 200

    async def test_manifest_valid(self, client: AsyncClient) -> None:
        data = (await client.get("/manifest.json")).json()
        assert data["name"] == "Silas"
        assert data["display"] == "standalone"
        assert data["start_url"] == "/"

    async def test_manifest_theme_matches_bg(self, client: AsyncClient) -> None:
        data = (await client.get("/manifest.json")).json()
        # Background should be our Quiet bg color
        assert data["background_color"] in ("#0a0f1e", "#0b1020")

    async def test_manifest_icons(self, client: AsyncClient) -> None:
        data = (await client.get("/manifest.json")).json()
        sizes = {i["sizes"] for i in data["icons"]}
        assert "192x192" in sizes
        assert "512x512" in sizes

    async def test_manifest_maskable_icon(self, client: AsyncClient) -> None:
        data = (await client.get("/manifest.json")).json()
        maskable = [i for i in data["icons"] if i.get("purpose") == "maskable"]
        assert len(maskable) >= 1


# ── Service Worker ──


class TestServiceWorker:
    async def test_sw_served(self, client: AsyncClient) -> None:
        resp = await client.get("/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]

    async def test_sw_cache_name(self, client: AsyncClient) -> None:
        assert "CACHE_NAME" in (await client.get("/sw.js")).text

    async def test_sw_caches_shell(self, client: AsyncClient) -> None:
        text = (await client.get("/sw.js")).text
        for asset in ["/", "/style.css", "/app.js", "/manifest.json"]:
            assert asset in text

    async def test_sw_skips_ws(self, client: AsyncClient) -> None:
        assert "/ws" in (await client.get("/sw.js")).text

    async def test_sw_handlers(self, client: AsyncClient) -> None:
        text = (await client.get("/sw.js")).text
        assert "install" in text
        assert "activate" in text
        assert "fetch" in text


# ── Icons ──


class TestIcons:
    async def test_icon_192(self, client: AsyncClient) -> None:
        resp = await client.get("/icons/icon-192.png")
        assert resp.status_code == 200
        assert "image/png" in resp.headers["content-type"]

    async def test_icon_512(self, client: AsyncClient) -> None:
        assert (await client.get("/icons/icon-512.png")).status_code == 200

    async def test_icon_maskable(self, client: AsyncClient) -> None:
        assert (await client.get("/icons/icon-512-maskable.png")).status_code == 200


# ── App JS Features ──


class TestAppJsFeatures:
    async def test_sw_registration(self, client: AsyncClient) -> None:
        text = (await client.get("/app.js")).text
        assert "serviceWorker" in text
        assert "register" in text

    async def test_install_prompt(self, client: AsyncClient) -> None:
        assert "beforeinstallprompt" in (await client.get("/app.js")).text

    async def test_ws_reconnect(self, client: AsyncClient) -> None:
        text = (await client.get("/app.js")).text
        assert "scheduleReconnect" in text
        assert "RECONNECT_BASE_MS" in text
        assert "RECONNECT_MAX_MS" in text

    async def test_exponential_backoff(self, client: AsyncClient) -> None:
        assert "Math.pow" in (await client.get("/app.js")).text

    async def test_network_events(self, client: AsyncClient) -> None:
        text = (await client.get("/app.js")).text
        assert '"online"' in text
        assert '"offline"' in text

    async def test_connection_status(self, client: AsyncClient) -> None:
        text = (await client.get("/app.js")).text
        assert "setConnectionStatus" in text
        assert "connected" in text
        assert "offline" in text

    async def test_empty_state_hides(self, client: AsyncClient) -> None:
        assert "hideEmptyState" in (await client.get("/app.js")).text

    async def test_thinking_indicator(self, client: AsyncClient) -> None:
        text = (await client.get("/app.js")).text
        assert "addThinking" in text
        assert "removeThinking" in text
        assert "thinking-dot" in text

    async def test_history_fade(self, client: AsyncClient) -> None:
        text = (await client.get("/app.js")).text
        assert "applyHistoryFade" in text
        assert "history-far" in text
        assert "history-mid" in text
        assert "history-recent" in text

    async def test_textarea_auto_resize(self, client: AsyncClient) -> None:
        text = (await client.get("/app.js")).text
        assert "scrollHeight" in text

    async def test_enter_sends_shift_enter_newline(self, client: AsyncClient) -> None:
        text = (await client.get("/app.js")).text
        assert "shiftKey" in text
        assert "Enter" in text

    async def test_html_escaping(self, client: AsyncClient) -> None:
        assert "escapeHtml" in (await client.get("/app.js")).text

    async def test_no_chat_bubble_classes(self, client: AsyncClient) -> None:
        """Quiet design: agent messages have no bubble/container classes."""
        text = (await client.get("/app.js")).text
        assert "msg-you" not in text
        assert "msg-silas" not in text

    async def test_user_messages_right_aligned(self, client: AsyncClient) -> None:
        text = (await client.get("/app.js")).text
        assert "msg-user" in text

    async def test_agent_messages_full_width(self, client: AsyncClient) -> None:
        text = (await client.get("/app.js")).text
        assert "msg-agent" in text or "createAgentMessageElement" in text


# ── CSS Design Tokens ──


class TestCssTokens:
    async def test_css_served(self, client: AsyncClient) -> None:
        assert (await client.get("/style.css")).status_code == 200

    async def test_bg_color(self, client: AsyncClient) -> None:
        assert "#0a0f1e" in (await client.get("/style.css")).text

    async def test_tint_color(self, client: AsyncClient) -> None:
        assert "#7ecbff" in (await client.get("/style.css")).text

    async def test_glass_component(self, client: AsyncClient) -> None:
        assert ".glass" in (await client.get("/style.css")).text

    async def test_breathe_animation(self, client: AsyncClient) -> None:
        assert "breathe" in (await client.get("/style.css")).text

    async def test_msg_enter_animation(self, client: AsyncClient) -> None:
        assert "msg-enter" in (await client.get("/style.css")).text

    async def test_thinking_dot_animation(self, client: AsyncClient) -> None:
        assert "thinking-dot" in (await client.get("/style.css")).text

    async def test_history_fade_classes(self, client: AsyncClient) -> None:
        text = (await client.get("/style.css")).text
        assert "history-far" in text
        assert "history-mid" in text

    async def test_reduced_motion(self, client: AsyncClient) -> None:
        assert "prefers-reduced-motion" in (await client.get("/style.css")).text

    async def test_spring_easing(self, client: AsyncClient) -> None:
        text = (await client.get("/style.css")).text
        assert ".22" in text  # spring bezier (may be minified as .22 vs 0.22)
