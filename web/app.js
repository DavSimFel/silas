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

const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${wsProtocol}://${window.location.host}/ws`);

function addMessage(sender, text) {
  const row = document.createElement("article");
  row.className = `msg msg-${sender}`;
  row.textContent = text;
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

ws.addEventListener("open", () => {
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
  addMessage("system", "Disconnected.");
});

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text || ws.readyState !== WebSocket.OPEN) {
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
