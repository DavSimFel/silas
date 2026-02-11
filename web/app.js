// ── Silas PWA — Quiet Design ──

// --- Service Worker ---
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

// --- Install Prompt ---
let deferredInstallPrompt = null;
const installBtn = document.getElementById("install-btn");

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  if (installBtn) installBtn.classList.remove("hidden");
});

if (installBtn) {
  installBtn.addEventListener("click", async () => {
    if (!deferredInstallPrompt) return;
    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    installBtn.classList.add("hidden");
  });
}

window.addEventListener("appinstalled", () => {
  if (installBtn) installBtn.classList.add("hidden");
});

// --- DOM ---
const stream = document.getElementById("stream");
const messages = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");
const composer = document.getElementById("composer");
const input = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const workStatus = document.getElementById("work-status");

let messageCount = 0;

// --- Auto-resize textarea ---
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 132) + "px";

  // Show/hide send button
  const hasContent = input.value.trim().length > 0;
  sendBtn.classList.toggle("opacity-0", !hasContent);
  sendBtn.classList.toggle("pointer-events-none", !hasContent);
  sendBtn.classList.toggle("opacity-100", hasContent);
  sendBtn.classList.toggle("pointer-events-auto", hasContent);
});

// --- WebSocket ---
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
let ws = null;
let reconnectAttempt = 0;
let reconnectTimer = null;

function setConnectionStatus(state) {
  const colors = {
    connected: "bg-status-green",
    connecting: "bg-status-amber",
    offline: "bg-status-red",
  };

  statusDot.className = "w-2 h-2 rounded-full transition-colors duration-200";
  statusDot.classList.add(colors[state] || colors.offline);
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  setConnectionStatus("connecting");
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${window.location.host}/ws`);

  ws.addEventListener("open", () => {
    reconnectAttempt = 0;
    setConnectionStatus("connected");
    // No "Connected." message — Quiet design: status dot is sufficient
  });

  ws.addEventListener("message", (event) => {
    removeThinking();
    try {
      const data = JSON.parse(event.data);
      if (data.type === "message") {
        addMessage("agent", data.text ?? "");
        return;
      }
    } catch (_) {}
    addMessage("agent", String(event.data));
  });

  ws.addEventListener("close", () => {
    setConnectionStatus("offline");
    scheduleReconnect();
  });

  ws.addEventListener("error", () => {});
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, reconnectAttempt), RECONNECT_MAX_MS);
  reconnectAttempt++;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

// --- Messages ---
function hideEmptyState() {
  if (emptyState && !emptyState.classList.contains("hidden")) {
    emptyState.classList.add("hidden");
  }
}

function addMessage(role, text) {
  hideEmptyState();
  messageCount++;

  const el = document.createElement("div");
  el.className = "msg-enter";

  if (role === "user") {
    // User: right-aligned, small, secondary
    el.innerHTML = `
      <div class="flex justify-end items-end gap-2">
        <p class="text-[13px] leading-[18px] text-text-secondary max-w-[85%] text-right">${escapeHtml(text)}</p>
        <span class="text-xs text-text-tertiary shrink-0">${timeLabel()}</span>
      </div>
    `;
  } else if (role === "agent") {
    // Agent: full-width, primary, no container
    el.innerHTML = `
      <div class="max-w-full">
        <div class="text-[15px] leading-[22px] text-text-primary whitespace-pre-wrap">${escapeHtml(text)}</div>
      </div>
    `;
  } else {
    // System: centered, tertiary
    el.innerHTML = `
      <p class="text-xs text-text-tertiary text-center">${escapeHtml(text)}</p>
    `;
  }

  messages.appendChild(el);
  applyHistoryFade();
  scrollToBottom();
}

function addThinking() {
  hideEmptyState();
  const el = document.createElement("div");
  el.id = "thinking";
  el.className = "msg-enter flex gap-1.5 py-1";
  el.innerHTML = `
    <span class="thinking-dot w-2 h-2 rounded-full bg-tint"></span>
    <span class="thinking-dot w-2 h-2 rounded-full bg-tint"></span>
    <span class="thinking-dot w-2 h-2 rounded-full bg-tint"></span>
  `;
  messages.appendChild(el);
  scrollToBottom();
  return el;
}

function removeThinking() {
  const el = document.getElementById("thinking");
  if (el) el.remove();
}

function applyHistoryFade() {
  const items = messages.querySelectorAll(":scope > div:not(#empty-state):not(#thinking)");
  const count = items.length;
  items.forEach((item, i) => {
    item.classList.remove("history-far", "history-mid", "history-recent");
    const age = count - 1 - i;
    if (age > 10) item.classList.add("history-far");
    else if (age > 4) item.classList.add("history-mid");
    else item.classList.add("history-recent");
  });
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    stream.scrollTop = stream.scrollHeight;
  });
}

function timeLabel() {
  const now = new Date();
  return now.getHours().toString().padStart(2, "0") + ":" + now.getMinutes().toString().padStart(2, "0");
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// --- Send ---
composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addMessage("system", "Reconnecting…");
    connect();
    return;
  }

  addMessage("user", text);
  const thinkingEl = addThinking();

  ws.send(JSON.stringify({ type: "message", sender_id: "owner", text }));

  // Clear input
  input.value = "";
  input.style.height = "auto";
  sendBtn.classList.add("opacity-0", "pointer-events-none");
  sendBtn.classList.remove("opacity-100", "pointer-events-auto");
});

// --- Keyboard ---
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    composer.requestSubmit();
  }
});

// --- Network ---
window.addEventListener("online", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) connect();
});
window.addEventListener("offline", () => setConnectionStatus("offline"));

// --- Init ---
connect();
input.focus();
