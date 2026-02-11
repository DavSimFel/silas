// --- Service Worker & Install Prompt ---
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

let deferredInstallPrompt = null;
const installBtn = document.getElementById("install-btn");

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  if (installBtn) installBtn.hidden = false;
});

if (installBtn) {
  installBtn.addEventListener("click", async () => {
    if (!deferredInstallPrompt) return;
    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    installBtn.hidden = true;
  });
}

window.addEventListener("appinstalled", () => {
  if (installBtn) installBtn.hidden = true;
});

// --- Chat ---
const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("composer");
const inputEl = document.getElementById("message-input");
const statusEl = document.getElementById("connection-status");

// --- WebSocket with auto-reconnect ---
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
let ws = null;
let reconnectAttempt = 0;
let reconnectTimer = null;

function setStatus(state) {
  if (!statusEl) return;
  statusEl.dataset.state = state;
  const labels = {
    connected: "Connected",
    connecting: "Connecting…",
    offline: "Offline",
  };
  statusEl.textContent = labels[state] || state;
  statusEl.hidden = state === "connected";
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  setStatus("connecting");
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${window.location.host}/ws`);

  ws.addEventListener("open", () => {
    reconnectAttempt = 0;
    setStatus("connected");
    addMessage("system", "Connected.");
  });

  ws.addEventListener("message", (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "message") {
        addMessage("silas", data.text ?? "");
        return;
      }
    } catch (_err) {
      // Fallback for plain text frames.
    }
    addMessage("silas", String(event.data));
  });

  ws.addEventListener("close", () => {
    setStatus("offline");
    scheduleReconnect();
  });

  ws.addEventListener("error", () => {
    // close event will fire after this — reconnect handled there
  });
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  const delay = Math.min(
    RECONNECT_BASE_MS * Math.pow(2, reconnectAttempt),
    RECONNECT_MAX_MS,
  );
  reconnectAttempt++;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

function addMessage(sender, text) {
  const row = document.createElement("article");
  row.className = `msg msg-${sender}`;
  row.textContent = text;
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addMessage("system", "Not connected. Retrying…");
    connect();
    return;
  }

  addMessage("you", text);
  ws.send(
    JSON.stringify({
      type: "message",
      sender_id: "owner",
      text,
    }),
  );
  inputEl.value = "";
});

// --- Network status ---
window.addEventListener("online", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) connect();
});
window.addEventListener("offline", () => setStatus("offline"));

// --- Init ---
connect();
