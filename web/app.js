// Silas PWA — Quiet Design Phase C

// --- Service Worker ---
let serviceWorkerRegistration = null;
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js")
    .then((registration) => {
      serviceWorkerRegistration = registration;
      if ("Notification" in window && Notification.permission === "granted") {
        subscribePush().catch(() => {});
      }
    })
    .catch(() => {});

  navigator.serviceWorker.addEventListener("message", (event) => {
    handleServiceWorkerMessage(event.data);
  });
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
const root = document.documentElement;
const stream = document.getElementById("stream");
const messages = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");
const messageRail = document.getElementById("message-rail");
const cardContainer = document.getElementById("card-container");
const approvalCardTemplateEl = document.getElementById("approval-card-template");
const batchReviewCardTemplateEl = document.getElementById("batch-review-card-template");
const suggestionCardTemplateEl = document.getElementById("suggestion-card-template");
const composer = document.getElementById("composer");
const input = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");
const slashPalette = document.getElementById("slash-palette");
const slashList = document.getElementById("slash-list");
const statusDot = document.getElementById("status-dot");
const statusStrip = document.getElementById("status-strip");
const workStatus = document.getElementById("work-status");
const sidePanel = document.getElementById("side-panel");
const sidePanelToggle = document.getElementById("side-panel-toggle");
const sidePanelClose = document.getElementById("side-panel-close");
const sideWorkItemsEl = document.getElementById("side-work-items");
const sessionInfoEl = document.getElementById("session-info");
const sideTabButtons = [...document.querySelectorAll("[data-side-tab]")];
const sideTabPanels = {
  memory: document.getElementById("panel-memory"),
  work: document.getElementById("panel-work"),
  session: document.getElementById("panel-session"),
};
const workPanel = document.getElementById("work-panel");
const workPanelBackdrop = document.getElementById("work-panel-backdrop");
const workPanelSheet = document.getElementById("work-panel-sheet");
const workPanelHandle = document.getElementById("work-panel-handle");
const workItemsEl = document.getElementById("work-items");
const shortcutOverlay = document.getElementById("shortcut-overlay");
const shortcutSheet = document.getElementById("shortcut-sheet");
const shortcutClose = document.getElementById("shortcut-close");
const liveRegion = document.getElementById("live-region");
const sessionTabsEl = document.getElementById("session-tabs");
const newSideSessionBtn = document.getElementById("new-side-session-btn");
const notificationPromptEl = document.getElementById("notification-prompt");
const notificationEnableBtn = document.getElementById("notification-enable-btn");
const notificationDismissBtn = document.getElementById("notification-dismiss-btn");
const onboardingOverlay = document.getElementById("onboarding-overlay");
const onboardingCard = document.getElementById("onboarding-card");
const onboardingForm = document.getElementById("onboarding-form");
const onboardingStepIndicator = document.getElementById("onboarding-step-indicator");
const onboardingStepOne = document.getElementById("onboarding-step-1");
const onboardingStepTwo = document.getElementById("onboarding-step-2");
const onboardingAgentNameInput = document.getElementById("onboarding-agent-name");
const onboardingApiKeyInput = document.getElementById("onboarding-api-key");
const onboardingNextBtn = document.getElementById("onboarding-next-btn");
const onboardingBackBtn = document.getElementById("onboarding-back-btn");
const onboardingFinishBtn = document.getElementById("onboarding-finish-btn");
const onboardingError = document.getElementById("onboarding-error");

const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

let messageCount = 0;
let elapsedTimer = null;
let connectionState = "connecting";
let panelVisiblePercent = 0;
let sidePanelOpen = false;
let sidePanelTab = "memory";
let slashPaletteOpen = false;
let slashSelectedIndex = 0;
let slashFilteredCommands = [];
let workPanelFocusReturn = null;
let shortcutFocusReturn = null;
let shortcutOpen = false;
let activeStreamMessageEl = null;
let hasRequestedNotificationPermission = false;
let notificationPromptDismissed = false;
let onboardingStep = 1;
let onboardingBusy = false;
let onboardingOpen = false;

const LONG_MESSAGE_THRESHOLD = 300;
const STREAM_CHUNK_TYPES = new Set(["stream_chunk", "message_chunk"]);
const STREAM_DONE_TYPES = new Set(["message_done", "stream_done", "completion_done"]);
const copyResetTimers = new WeakMap();
const NOTIFICATION_PROMPT_DISMISS_KEY = "silas.push.prompt.dismissed";
const ONBOARDING_FLAG_KEY = "silas_onboarded";
const SESSION_MAIN_KEY = "main";
const sessionsByKey = new Map();
let activeSessionKey = SESSION_MAIN_KEY;
let sideSessionOrdinal = 0;

const SHEET_SNAP_VISIBLE = {
  dismissed: 0,
  peek: 40,
  full: 85,
};

const workState = {
  items: [],
};

const cardState = {
  cards: new Map(),
  workItemByCardId: new Map(),
};

const MOTION = {
  instant: readDurationMs("--dur-instant", 100),
  fast: readDurationMs("--dur-fast", 200),
  default: readDurationMs("--dur-default", 300),
  slow: readDurationMs("--dur-slow", 400),
};
MOTION.collapse = Math.round(MOTION.default * 0.7);

const DEMO_APPROVAL_ITEMS = [
  {
    id: "approve-budget-draft",
    title: "Approve Q4 budget draft",
    risk: "medium",
    rationale: "Figures align with current workbook and this blocks sending.",
    details:
      "Intent: Send draft to finance. Consequence: Team sees this wording immediately. Alternatives: Delay send and review manually.",
    cta: {
      approve: "Approve",
      decline: "Decline",
    },
    confirmation: {
      approve: "\u2713 Draft approved",
      decline: "Declined \u00b7 Draft kept in workspace",
    },
    workItem: {
      title: "Budget approval needed",
      details: "Awaiting your decision before sending draft.",
      status: "needs_review",
      startedOffsetSec: 20,
    },
  },
];

const DESKTOP_QUERY = window.matchMedia("(min-width: 768px)");

const SLASH_COMMANDS = [
  {
    command: "/clear",
    description: "Clear conversation stream",
    run: () => clearConversation(),
  },
  {
    command: "/status",
    description: "Show current connection status",
    run: () => addMessage("system", statusSummary()),
  },
  {
    command: "/theme",
    description: "Theme toggle (reserved)",
    run: () => addMessage("system", "Theme toggle is reserved for a future phase."),
  },
  {
    command: "/help",
    description: "Show keyboard shortcut help",
    run: () => openShortcutOverlay(),
  },
];

function readLocalStorageValue(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

function writeLocalStorageValue(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (_) {}
}

function isOnboardingComplete() {
  return readLocalStorageValue(ONBOARDING_FLAG_KEY) === "true";
}

function setOnboardingStep(step) {
  onboardingStep = step === 2 ? 2 : 1;
  const firstStepActive = onboardingStep === 1;
  onboardingStepIndicator.textContent = `Step ${onboardingStep} of 2`;
  onboardingStepOne.classList.toggle("hidden", !firstStepActive);
  onboardingStepOne.setAttribute("aria-hidden", String(!firstStepActive));
  onboardingStepTwo.classList.toggle("hidden", firstStepActive);
  onboardingStepTwo.setAttribute("aria-hidden", String(firstStepActive));
}

function setOnboardingOpen(open) {
  onboardingOpen = open;
  if (!onboardingOverlay) return;

  if (open) {
    onboardingOverlay.hidden = false;
    requestAnimationFrame(() => {
      onboardingOverlay.classList.add("is-visible");
    });
    return;
  }

  onboardingOverlay.classList.remove("is-visible");
  const delay = prefersReducedMotion.matches ? 0 : MOTION.fast;
  setTimeout(() => {
    if (!onboardingOpen) {
      onboardingOverlay.hidden = true;
    }
  }, delay);
}

function setOnboardingBusy(isBusy) {
  onboardingBusy = isBusy;
  if (onboardingNextBtn) onboardingNextBtn.disabled = isBusy;
  if (onboardingBackBtn) onboardingBackBtn.disabled = isBusy;
  if (onboardingFinishBtn) {
    onboardingFinishBtn.disabled = isBusy;
    onboardingFinishBtn.textContent = isBusy ? "Finishing…" : "Finish";
  }
}

function setOnboardingError(message) {
  if (!onboardingError) return;
  const text = String(message ?? "").trim();
  onboardingError.textContent = text;
  onboardingError.classList.toggle("is-visible", text.length > 0);
}

function ownerNameForOnboarding() {
  const storedOwner = readLocalStorageValue("silas_owner_name");
  if (storedOwner && storedOwner.trim()) {
    return storedOwner.trim();
  }
  return "owner";
}

async function submitOnboarding() {
  if (onboardingBusy) return;

  const normalizedName = (onboardingAgentNameInput?.value || "").trim() || "Silas";
  if (onboardingAgentNameInput) onboardingAgentNameInput.value = normalizedName;

  const apiKey = (onboardingApiKeyInput?.value || "").trim();
  if (!apiKey) {
    setOnboardingError("Please enter your OpenRouter API key.");
    onboardingApiKeyInput?.focus();
    return;
  }

  setOnboardingBusy(true);
  setOnboardingError("");

  try {
    const response = await fetch("/api/onboard", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        agent_name: normalizedName,
        api_key: apiKey,
        owner_name: ownerNameForOnboarding(),
      }),
    });

    if (!response.ok) {
      let message = "Unable to complete onboarding.";
      try {
        const payload = await response.json();
        if (payload?.detail && typeof payload.detail === "string") {
          message = payload.detail;
        }
      } catch (_) {}
      throw new Error(message);
    }

    writeLocalStorageValue(ONBOARDING_FLAG_KEY, "true");
    setOnboardingOpen(false);
    focusComposer();
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to complete onboarding.";
    setOnboardingError(message);
  } finally {
    setOnboardingBusy(false);
  }
}

function initOnboarding() {
  if (
    !onboardingOverlay ||
    !onboardingCard ||
    !onboardingForm ||
    !onboardingStepIndicator ||
    !onboardingStepOne ||
    !onboardingStepTwo ||
    !onboardingAgentNameInput ||
    !onboardingApiKeyInput
  ) {
    return true;
  }

  onboardingNextBtn?.addEventListener("click", () => {
    if (onboardingBusy) return;

    const normalizedName = onboardingAgentNameInput.value.trim();
    if (!normalizedName) {
      setOnboardingError("Please choose an agent name.");
      onboardingAgentNameInput.focus();
      return;
    }

    onboardingAgentNameInput.value = normalizedName;
    setOnboardingError("");
    setOnboardingStep(2);
    onboardingApiKeyInput.focus();
  });

  onboardingBackBtn?.addEventListener("click", () => {
    if (onboardingBusy) return;
    setOnboardingError("");
    setOnboardingStep(1);
    onboardingAgentNameInput.focus();
  });

  onboardingForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (onboardingStep === 1) {
      onboardingNextBtn?.click();
      return;
    }
    void submitOnboarding();
  });

  setOnboardingStep(1);
  setOnboardingBusy(false);
  setOnboardingError("");

  if (isOnboardingComplete()) {
    setOnboardingOpen(false);
    return true;
  }

  setOnboardingOpen(true);
  const focusDelay = prefersReducedMotion.matches ? 0 : MOTION.fast;
  setTimeout(() => {
    if (onboardingOpen) {
      onboardingAgentNameInput.focus();
      onboardingAgentNameInput.setSelectionRange(
        onboardingAgentNameInput.value.length,
        onboardingAgentNameInput.value.length,
      );
    }
  }, focusDelay);
  return false;
}

// --- Composer state ---
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
  updateComposerState();
  syncSlashPalette();
});

input.addEventListener("focus", () => {
  syncSlashPalette();
});

input.addEventListener("blur", () => {
  setTimeout(() => {
    if (!composer.contains(document.activeElement)) {
      closeSlashPalette();
    }
  }, 0);
});

function updateComposerState() {
  const hasContent = input.value.trim().length > 0;
  sendBtn.classList.toggle("opacity-0", !hasContent);
  sendBtn.classList.toggle("pointer-events-none", !hasContent);
  sendBtn.classList.toggle("opacity-100", hasContent);
  sendBtn.classList.toggle("pointer-events-auto", hasContent);
}

function focusComposer() {
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

function isEditableTarget(target) {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(target.closest("input, textarea, [contenteditable='true']"));
}

function clearConversation() {
  activeStreamMessageEl = null;
  removeThinking();
  messageRail?.replaceChildren();
  cardContainer?.replaceChildren();
  cardState.cards.clear();
  cardState.workItemByCardId.clear();
  emptyState.classList.remove("hidden");
  messageCount = 0;
  workState.items = [];
  renderWorkItems();
  applyHistoryFade();
  renderSessionInfo();
  scrollToBottom();
}

function statusSummary() {
  const activeCount = workState.items.filter((item) => item.status === "active").length;
  const needsReviewCount = workState.items.filter((item) => item.status === "needs_review").length;
  return `Connection: ${connectionState}. Active work: ${activeCount}. Needs review: ${needsReviewCount}.`;
}

function slashQueryFromInput() {
  const raw = input.value;
  if (!raw.startsWith("/")) return null;
  if (raw.includes("\n")) return null;
  return raw.slice(1).trim().toLowerCase();
}

function syncSlashPalette() {
  const query = slashQueryFromInput();
  const shouldOpen = document.activeElement === input && query !== null;
  if (!shouldOpen) {
    closeSlashPalette();
    return;
  }

  openSlashPalette(query);
}

function openSlashPalette(query = "") {
  slashPaletteOpen = true;
  const normalized = query.toLowerCase();
  slashFilteredCommands = SLASH_COMMANDS.filter((item) => {
    const command = item.command.slice(1).toLowerCase();
    const description = item.description.toLowerCase();
    return command.includes(normalized) || description.includes(normalized);
  });

  slashSelectedIndex = clamp(slashSelectedIndex, 0, Math.max(0, slashFilteredCommands.length - 1));
  slashPalette.hidden = false;
  renderSlashPalette();
}

function closeSlashPalette() {
  slashPaletteOpen = false;
  slashFilteredCommands = [];
  slashSelectedIndex = 0;
  slashPalette.hidden = true;
  slashList.innerHTML = "";
}

function moveSlashSelection(delta) {
  if (!slashPaletteOpen || slashFilteredCommands.length === 0) return;
  slashSelectedIndex = (slashSelectedIndex + delta + slashFilteredCommands.length) % slashFilteredCommands.length;
  renderSlashPalette();
}

function renderSlashPalette() {
  if (!slashPaletteOpen) return;

  if (slashFilteredCommands.length === 0) {
    slashList.innerHTML = `<li class="slash-empty" role="option" aria-disabled="true">No commands found</li>`;
    return;
  }

  slashList.innerHTML = slashFilteredCommands
    .map((item, index) => {
      const active = index === slashSelectedIndex;
      return `
        <li role="option" aria-selected="${String(active)}">
          <button
            type="button"
            class="slash-option ${active ? "is-active" : ""}"
            data-slash-index="${index}"
            aria-label="${escapeHtml(item.command)}: ${escapeHtml(item.description)}"
          >
            <span class="slash-command">${escapeHtml(item.command)}</span>
            <span class="slash-description">${escapeHtml(item.description)}</span>
          </button>
        </li>
      `;
    })
    .join("");

  slashList.querySelectorAll("[data-slash-index]").forEach((button) => {
    button.addEventListener("click", () => {
      const index = Number.parseInt(button.getAttribute("data-slash-index") || "0", 10);
      runSlashCommand(index);
    });
  });
}

function runSlashCommand(index) {
  const selected = slashFilteredCommands[index];
  if (!selected) return;

  input.value = "";
  input.style.height = "auto";
  updateComposerState();
  closeSlashPalette();
  selected.run();
}

// --- WebSocket ---
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
let ws = null;
let reconnectAttempt = 0;
let reconnectTimer = null;

function setConnectionStatus(state) {
  connectionState = state;
  const colors = {
    connected: "bg-status-green",
    connecting: "bg-status-amber",
    reconnecting: "bg-status-amber",
    offline: "bg-status-red",
  };

  statusDot.className = "status-dot w-2 h-2 rounded-full transition-colors duration-200";
  statusDot.classList.add(colors[state] || colors.offline);
  statusDot.classList.toggle("status-dot-pulse", state === "reconnecting");
  statusDot.setAttribute("aria-label", connectionAriaLabel(state));
  updateStatusStrip();
  renderSessionInfo();
}

function connectionAriaLabel(state) {
  if (state === "connected") return "Connected";
  if (state === "reconnecting") return "Reconnecting";
  if (state === "connecting") return "Connecting";
  return "Offline";
}

function connect(isReconnect = reconnectAttempt > 0) {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  setConnectionStatus(isReconnect ? "reconnecting" : "connecting");
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${window.location.host}/ws`);

  ws.addEventListener("open", () => {
    reconnectAttempt = 0;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    setConnectionStatus("connected");
  });

  ws.addEventListener("message", (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "stream_start") {
        startStreamingMessage();
        return;
      }

      if (STREAM_CHUNK_TYPES.has(data.type)) {
        addStreamChunk(data.text ?? "");
        return;
      }

      if (data.type === "stream_end") {
        finalizeStreamingMessage();
        completeOldestActiveWork();
        return;
      }

      if (STREAM_DONE_TYPES.has(data.type)) {
        finalizeStreamingMessage(data.text ?? null);
        completeOldestActiveWork();
        return;
      }

      if (data.type === "message") {
        if (activeStreamMessageEl) {
          finalizeStreamingMessage(data.text ?? null);
        } else {
          addMessage("agent", data.text ?? "");
        }
        completeOldestActiveWork();
        return;
      }

      if (data.type === "approval_card" || data.type === "action_card") {
        finalizeStreamingMessage();
        removeThinking();
        renderActionCards([data.card || data]);
        return;
      }

      if (data.type === "work_state" && Array.isArray(data.items)) {
        setWorkItems(data.items);
        return;
      }
    } catch (_) {
      // fall through to raw text rendering
    }
    finalizeStreamingMessage();
    addMessage("agent", String(event.data));
    completeOldestActiveWork();
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
  reconnectAttempt += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (!navigator.onLine) {
      setConnectionStatus("offline");
      scheduleReconnect();
      return;
    }
    connect(true);
  }, delay);
}

// --- Messages ---
function hideEmptyState() {
  if (emptyState && !emptyState.classList.contains("hidden")) {
    emptyState.classList.add("hidden");
  }
}

function addMessage(role, text) {
  const value = String(text ?? "");

  hideEmptyState();
  removeThinking();

  if (role === "agent" && activeStreamMessageEl && activeStreamMessageEl.isConnected) {
    finalizeStreamingMessage(value);
    return;
  }

  let el = null;
  if (role === "agent") {
    el = createAgentMessageElement(value);
  } else if (role === "user") {
    el = createUserMessageElement(value);
  } else {
    el = createSystemMessageElement(value);
  }

  if (!el) return;
  messageCount += 1;
  messageRail?.appendChild(el);
  applyHistoryFade();
  scrollToBottom();
  announceMessage(role, value);
  renderSessionInfo();
}

function addThinking() {
  hideEmptyState();
  removeThinking();
  const el = document.createElement("div");
  el.id = "thinking";
  el.className = "msg-enter thinking-slot py-1";
  el.innerHTML = `
    <div class="thinking-inline" aria-hidden="true">
      <span class="thinking-dot w-2 h-2 rounded-full"></span>
      <span class="thinking-dot w-2 h-2 rounded-full"></span>
      <span class="thinking-dot w-2 h-2 rounded-full"></span>
    </div>
  `;
  messageRail?.appendChild(el);
  applyHistoryFade();
  scrollToBottom();
  return el;
}

function removeThinking() {
  const el = document.getElementById("thinking");
  if (el) el.remove();
}

function createUserMessageElement(text) {
  const el = document.createElement("div");
  el.className = "msg-enter msg-row msg-user";
  el.innerHTML = `
    <p class="msg-main">${escapeHtml(text)}</p>
    <span class="msg-time" aria-hidden="true">${timeLabel()}</span>
  `;
  wireMessageMetaReveal(el);
  return el;
}

function createSystemMessageElement(text) {
  const el = document.createElement("div");
  el.className = "msg-enter msg-system";
  el.innerHTML = `<p class="msg-system-text">${escapeHtml(text)}</p>`;
  return el;
}

function createAgentMessageElement(text, options = {}) {
  const { streaming = false } = options;
  const el = document.createElement("div");
  el.className = "msg-enter msg-row msg-agent";
  if (streaming) {
    el.dataset.streaming = "true";
    activeStreamMessageEl = el;
  }

  el.innerHTML = `
    <div class="msg-main" data-agent-content></div>
    <span class="msg-time" aria-hidden="true">${timeLabel()}</span>
  `;

  wireMessageMetaReveal(el);
  renderAgentMessageContent(el, text, { streaming });
  return el;
}

function renderAgentMessageContent(el, text, options = {}) {
  const { streaming = false } = options;
  const container = el.querySelector("[data-agent-content]");
  if (!container) return;

  const fullText = String(text ?? "");
  const expanded = el.dataset.expanded === "true";
  const isLong = fullText.length > LONG_MESSAGE_THRESHOLD;
  const shouldClamp = isLong && !expanded && !streaming;
  const preview = shouldClamp
    ? `${fullText.slice(0, LONG_MESSAGE_THRESHOLD).trimEnd()}…`
    : fullText;

  el.dataset.fullText = fullText;
  container.innerHTML = `
    <div class="md-prose" data-md-prose>${renderMarkdown(preview)}</div>
    ${
      isLong && !streaming
        ? `<button type="button" class="msg-expand-toggle" data-msg-expand>${
          expanded ? "Show less" : "Show more"
        }</button>`
        : ""
    }
  `;

  if (streaming) {
    const prose = container.querySelector("[data-md-prose]");
    if (prose) {
      const cursor = document.createElement("span");
      cursor.className = "stream-cursor";
      cursor.setAttribute("aria-hidden", "true");
      prose.appendChild(cursor);
    }
  }

  const toggle = container.querySelector("[data-msg-expand]");
  if (toggle) {
    toggle.addEventListener("click", () => {
      el.dataset.expanded = String(!(el.dataset.expanded === "true"));
      renderAgentMessageContent(el, el.dataset.fullText || "", { streaming: false });
      applyHistoryFade();
      scrollToBottom();
    });
  }

  wireMarkdownInteractions(container);
}

function startStreamingMessage() {
  hideEmptyState();
  removeThinking();

  if (activeStreamMessageEl && activeStreamMessageEl.isConnected) {
    finalizeStreamingMessage();
  }

  const target = createAgentMessageElement("", { streaming: true });
  messageRail?.appendChild(target);
  messageCount += 1;
  applyHistoryFade();
  scrollToBottom();
  renderSessionInfo();
}

function addStreamChunk(text) {
  const chunk = String(text ?? "");
  if (!chunk) return;

  hideEmptyState();
  removeThinking();

  let target = activeStreamMessageEl;
  if (!target || !target.isConnected) {
    target = createAgentMessageElement("", { streaming: true });
    messageRail?.appendChild(target);
    messageCount += 1;
  }

  const nextText = `${target.dataset.fullText || ""}${chunk}`;
  renderAgentMessageContent(target, nextText, { streaming: true });
  animateStreamingChunk(target);
  applyHistoryFade();
  scrollToBottom();
  renderSessionInfo();
}

function animateStreamingChunk(target) {
  if (!target || prefersReducedMotion.matches) return;
  const prose = target.querySelector("[data-md-prose]");
  if (!prose) return;

  prose.animate(
    [{ opacity: 0.56 }, { opacity: 1 }],
    {
      duration: MOTION.fast,
      easing: "ease-out",
    },
  );
}

function finalizeStreamingMessage(text = null) {
  const target = activeStreamMessageEl && activeStreamMessageEl.isConnected
    ? activeStreamMessageEl
    : null;
  const hasIncomingText = text !== null && text !== undefined;

  if (!target) {
    if (hasIncomingText && String(text).trim()) {
      addMessage("agent", String(text));
    }
    return;
  }

  const finalText = hasIncomingText ? String(text) : (target.dataset.fullText || "");
  target.removeAttribute("data-streaming");
  activeStreamMessageEl = null;
  renderAgentMessageContent(target, finalText, { streaming: false });
  announceMessage("agent", finalText);
  applyHistoryFade();
  scrollToBottom();
  renderSessionInfo();
}

function wireMessageMetaReveal(messageEl) {
  if (!messageEl || messageEl.dataset.metaRevealBound === "true") return;
  messageEl.dataset.metaRevealBound = "true";

  messageEl.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "touch") return;
    if (event.target instanceof Element && event.target.closest("button, a")) return;
    messageEl.classList.add("show-meta");

    const currentTimer = Number.parseInt(messageEl.dataset.metaRevealTimer || "0", 10);
    if (currentTimer) {
      clearTimeout(currentTimer);
    }

    const timer = window.setTimeout(() => {
      messageEl.classList.remove("show-meta");
      messageEl.dataset.metaRevealTimer = "0";
    }, 1600);
    messageEl.dataset.metaRevealTimer = String(timer);
  });
}

function wireMarkdownInteractions(scopeEl) {
  if (!scopeEl) return;
  scopeEl.querySelectorAll("[data-code-copy]").forEach((button) => {
    if (button.dataset.copyBound === "true") return;
    button.dataset.copyBound = "true";
    button.addEventListener("click", async () => {
      const codeBlock = button.closest("[data-code-block]");
      const code = codeBlock?.querySelector("code")?.textContent || "";
      if (!code) return;

      const copied = await copyToClipboard(code);
      if (!copied) return;

      button.classList.add("is-copied");
      button.textContent = "Copied";

      const existingTimer = copyResetTimers.get(button);
      if (existingTimer) {
        clearTimeout(existingTimer);
      }

      const timer = window.setTimeout(() => {
        button.classList.remove("is-copied");
        button.textContent = "Copy";
        copyResetTimers.delete(button);
      }, 1200);
      copyResetTimers.set(button, timer);
    });
  });
}

async function copyToClipboard(text) {
  const content = String(text ?? "");
  if (!content) return false;

  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(content);
      return true;
    } catch (_) {
      // fall through to legacy copy
    }
  }

  const temp = document.createElement("textarea");
  temp.value = content;
  temp.setAttribute("readonly", "true");
  temp.style.position = "absolute";
  temp.style.left = "-9999px";
  document.body.appendChild(temp);
  temp.select();

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (_) {
    copied = false;
  }

  temp.remove();
  return copied;
}

function renderMarkdown(text) {
  const source = String(text ?? "").replace(/\r\n?/g, "\n");
  if (!source.trim()) {
    return "<p></p>";
  }

  const lines = source.split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^```([\w.+-]+)?\s*$/);
    if (fence) {
      const language = (fence[1] || "").trim();
      const codeLines = [];
      index += 1;

      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }

      if (index < lines.length && /^```\s*$/.test(lines[index])) {
        index += 1;
      }

      blocks.push(renderCodeBlock(codeLines.join("\n"), language));
      continue;
    }

    if (/^\s*---\s*$/.test(line)) {
      blocks.push('<hr class="md-hr" />');
      index += 1;
      continue;
    }

    const heading = line.match(/^\s{0,3}(#{1,3})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2].trim())}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }

      const quoteText = quoteLines.join(" ").trim();
      blocks.push(`<blockquote class="md-blockquote"><p>${renderInlineMarkdown(quoteText)}</p></blockquote>`);
      continue;
    }

    if (/^\s*-\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*-\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*-\s+/, "").trim());
        index += 1;
      }

      blocks.push(
        `<ul class="md-list md-list-ul">${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`,
      );
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      let start = 1;

      while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
        const ordered = lines[index].match(/^\s*(\d+)\.\s+(.*)$/);
        if (ordered) {
          if (items.length === 0) {
            start = Number.parseInt(ordered[1], 10) || 1;
          }
          items.push(ordered[2].trim());
        }
        index += 1;
      }

      const startAttr = start > 1 ? ` start="${start}"` : "";
      blocks.push(
        `<ol class="md-list md-list-ol"${startAttr}>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ol>`,
      );
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length) {
      const candidate = lines[index];
      if (!candidate.trim()) break;
      if (isMarkdownBlockStart(candidate) && paragraphLines.length > 0) break;
      paragraphLines.push(candidate.trim());
      index += 1;
    }

    const paragraph = paragraphLines.join(" ");
    if (paragraph) {
      blocks.push(`<p>${renderInlineMarkdown(paragraph)}</p>`);
    }
  }

  return blocks.join("");
}

function isMarkdownBlockStart(line) {
  return (
    /^\s*```/.test(line) ||
    /^\s*---\s*$/.test(line) ||
    /^\s*>\s?/.test(line) ||
    /^\s*-\s+/.test(line) ||
    /^\s*\d+\.\s+/.test(line) ||
    /^\s{0,3}(#{1,3})\s+/.test(line)
  );
}

function renderCodeBlock(code, language) {
  const label = language ? `<span class="md-code-lang">${escapeHtml(language)}</span>` : "<span></span>";
  return `
    <div class="md-code-block" data-code-block>
      <div class="md-code-meta">
        ${label}
        <button type="button" class="md-code-copy" data-code-copy aria-label="Copy code">Copy</button>
      </div>
      <pre><code>${escapeHtml(code)}</code></pre>
    </div>
  `;
}

function renderInlineMarkdown(text) {
  const source = String(text ?? "");
  let index = 0;
  let html = "";

  while (index < source.length) {
    if (source.startsWith("**", index)) {
      const close = source.indexOf("**", index + 2);
      if (close !== -1) {
        html += `<strong>${renderInlineMarkdown(source.slice(index + 2, close))}</strong>`;
        index = close + 2;
        continue;
      }
    }

    if (source[index] === "*") {
      const close = source.indexOf("*", index + 1);
      if (close !== -1) {
        html += `<em>${renderInlineMarkdown(source.slice(index + 1, close))}</em>`;
        index = close + 1;
        continue;
      }
    }

    if (source[index] === "`") {
      const close = source.indexOf("`", index + 1);
      if (close !== -1) {
        html += `<code class="md-inline-code">${escapeHtml(source.slice(index + 1, close))}</code>`;
        index = close + 1;
        continue;
      }
    }

    if (source[index] === "[") {
      const closeBracket = source.indexOf("]", index + 1);
      if (closeBracket !== -1 && source[closeBracket + 1] === "(") {
        const closeParen = source.indexOf(")", closeBracket + 2);
        if (closeParen !== -1) {
          const label = source.slice(index + 1, closeBracket);
          const href = source.slice(closeBracket + 2, closeParen).trim();
          const safeUrl = sanitizeUrl(href);
          if (safeUrl) {
            html += `<a class="md-link" href="${escapeAttribute(safeUrl)}" target="_blank" rel="noopener">${renderInlineMarkdown(label)}</a>`;
          } else {
            html += escapeHtml(source.slice(index, closeParen + 1));
          }
          index = closeParen + 1;
          continue;
        }
      }
    }

    html += escapeHtml(source[index]);
    index += 1;
  }

  return html;
}

function sanitizeUrl(url) {
  if (!url) return null;

  try {
    const parsed = new URL(url, window.location.origin);
    if (!["http:", "https:", "mailto:"].includes(parsed.protocol)) {
      return null;
    }
    return parsed.href;
  } catch (_) {
    return null;
  }
}

function announceMessage(role, text) {
  if (!liveRegion) return;
  const value = String(text || "").trim();
  if (!value) return;
  const prefix = role === "agent" ? "Silas" : role === "user" ? "You" : "Status";
  liveRegion.textContent = `${prefix}: ${value}`;
}

function applyHistoryFade() {
  const items = messageRail?.querySelectorAll(":scope > div:not(#thinking)") || [];
  const count = items.length;
  items.forEach((item, i) => {
    item.classList.remove("history-far", "history-mid", "history-recent");
    const age = count - 1 - i;
    const opacity = historyOpacity(age);
    item.style.opacity = String(opacity);

    if (opacity >= 0.85) item.classList.add("history-recent");
    else if (opacity >= 0.65) item.classList.add("history-mid");
    else item.classList.add("history-far");
  });
}

function historyOpacity(age) {
  if (age <= 0) return 1;
  if (age === 1) return 0.85;
  return Math.max(0.5, +(0.85 - (age - 1) * 0.07).toFixed(2));
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    stream.scrollTop = stream.scrollHeight;
  });
}

function timeLabel() {
  const now = new Date();
  return `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function escapeAttribute(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

// --- Cards ---
function renderActionCards(cards) {
  if (!Array.isArray(cards)) return;

  cards.forEach((card) => {
    if (!card || !card.id) return;

    hideEmptyState();
    cardState.cards.set(card.id, card);

    const wrapper = document.createElement("div");
    wrapper.className = "msg-enter";
    wrapper.dataset.cardId = card.id;

    const risk = card.risk || "low";
    const hasDetails = Boolean(card.details);

    wrapper.innerHTML = `
      <article class="glass-card" data-card-id="${escapeHtml(card.id)}" data-risk="${escapeHtml(risk)}">
        <div class="glass-card-header">
          <h3 class="glass-card-title">${escapeHtml(card.title || "Needs approval")}</h3>
          <span class="glass-card-risk ${riskClass(risk)}" aria-hidden="true"></span>
        </div>
        <p class="glass-card-rationale">${escapeHtml(card.rationale || "Review and decide.")}</p>
        ${hasDetails ? expandableTemplate("Details", card.details) : ""}
        <div class="glass-card-actions">
          <button type="button" class="card-cta-primary" data-card-action="approve">${escapeHtml(card.cta?.approve || "Approve")}</button>
          <button type="button" class="card-cta-secondary" data-card-action="decline">${escapeHtml(card.cta?.decline || "Decline")}</button>
        </div>
      </article>
    `;

    cardContainer?.appendChild(wrapper);

    const article = wrapper.querySelector(".glass-card");
    wireExpandables(wrapper);

    wrapper.querySelectorAll("[data-card-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.getAttribute("data-card-action") || "approve";
        handleCardDecision(card.id, action, article);
      });
    });

    if (card.workItem) {
      const workId = `work-${card.id}`;
      cardState.workItemByCardId.set(card.id, workId);
      upsertWorkItem({
        id: workId,
        title: card.workItem.title || card.title,
        details: card.workItem.details || card.rationale || "",
        status: card.workItem.status || "needs_review",
        startedAt: Date.now() - ((card.workItem.startedOffsetSec || 0) * 1000),
      });
    }
  });

  applyHistoryFade();
  renderWorkItems();
  updateStatusStrip();
  scrollToBottom();
}

function handleCardDecision(cardId, action, cardEl) {
  const card = cardState.cards.get(cardId);
  if (!card || !cardEl) return;

  const normalizedAction = action === "decline" ? "decline" : "approve";

  if (ws && ws.readyState === WebSocket.OPEN) {
    try {
      ws.send(JSON.stringify({ type: "approval_response", card_id: cardId, action: normalizedAction }));
    } catch (_) {}
  }

  const confirmation =
    (normalizedAction === "approve" ? card.confirmation?.approve : card.confirmation?.decline) ||
    (normalizedAction === "approve" ? "\u2713 Approved" : "Declined");

  const startHeight = cardEl.getBoundingClientRect().height;
  cardEl.style.height = `${startHeight}px`;
  cardEl.style.overflow = "hidden";

  cardEl.innerHTML = `<p class="glass-card-confirmation">${escapeHtml(confirmation)}</p>`;

  const targetHeight = cardEl.scrollHeight;
  requestAnimationFrame(() => {
    cardEl.classList.add("is-deciding");
    cardEl.style.transition = `height ${MOTION.fast}ms var(--ease-out), opacity ${MOTION.fast}ms var(--ease-out)`;
    cardEl.style.height = `${targetHeight}px`;
  });

  const cleanup = () => {
    cardEl.style.height = "";
    cardEl.style.overflow = "";
    cardEl.style.transition = "";
  };

  setTimeout(() => {
    cleanup();
    cardEl.classList.remove("is-deciding");
  }, MOTION.fast + 40);

  const linkedWorkId = cardState.workItemByCardId.get(cardId);
  if (linkedWorkId) {
    upsertWorkItem({
      id: linkedWorkId,
      status: "completed",
      details: confirmation,
      completedAt: Date.now(),
    });
  }

  setTimeout(() => {
    cardEl.classList.add("is-confirmation-quiet");
    applyHistoryFade();
  }, 3000);

  renderWorkItems();
  updateStatusStrip();
}

function riskClass(risk) {
  if (risk === "medium") return "risk-medium";
  if (risk === "high") return "risk-high";
  if (risk === "irreversible") return "risk-irreversible";
  return "risk-low";
}

// --- Expandable sections ---
function expandableTemplate(label, content) {
  return `
    <div class="expandable" data-expandable>
      <button type="button" class="expand-toggle" data-expand-toggle aria-expanded="false">
        <span class="expand-chevron" aria-hidden="true">\u25b8</span>
        ${escapeHtml(label)}
      </button>
      <div class="expand-panel" data-expand-panel hidden>
        <div class="expand-content" data-expand-content>${escapeHtml(content)}</div>
      </div>
    </div>
  `;
}

function wireExpandables(scopeEl) {
  const blocks = scopeEl.querySelectorAll("[data-expandable]");
  blocks.forEach((block) => {
    const toggle = block.querySelector("[data-expand-toggle]");
    const panel = block.querySelector("[data-expand-panel]");
    const content = block.querySelector("[data-expand-content]");

    if (!toggle || !panel) return;

    toggle.addEventListener("click", () => {
      const isOpen = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!isOpen));
      animateExpandable(panel, content, !isOpen);
    });
  });
}

function animateExpandable(panel, content, open) {
  if (prefersReducedMotion.matches) {
    panel.hidden = !open;
    if (content) content.classList.toggle("is-visible", open);
    return;
  }

  if (open) {
    panel.hidden = false;
    if (content) content.classList.remove("is-visible");

    panel.style.overflow = "hidden";
    panel.style.height = "0px";
    panel.style.transition = `height ${MOTION.default}ms var(--ease-spring)`;

    const targetHeight = panel.scrollHeight;
    requestAnimationFrame(() => {
      panel.style.height = `${targetHeight}px`;
    });

    const onOpenEnd = (event) => {
      if (event.propertyName !== "height") return;
      panel.removeEventListener("transitionend", onOpenEnd);
      panel.style.height = "";
      panel.style.overflow = "";
      panel.style.transition = "";

      if (content) {
        setTimeout(() => {
          content.classList.add("is-visible");
        }, MOTION.instant);
      }
    };

    panel.addEventListener("transitionend", onOpenEnd);
    return;
  }

  if (content) content.classList.remove("is-visible");

  const startHeight = panel.getBoundingClientRect().height;
  panel.style.overflow = "hidden";
  panel.style.height = `${startHeight}px`;
  panel.style.transition = `height ${MOTION.collapse}ms var(--ease-spring)`;

  requestAnimationFrame(() => {
    panel.style.height = "0px";
  });

  const onCloseEnd = (event) => {
    if (event.propertyName !== "height") return;
    panel.removeEventListener("transitionend", onCloseEnd);
    panel.hidden = true;
    panel.style.height = "";
    panel.style.overflow = "";
    panel.style.transition = "";
  };

  panel.addEventListener("transitionend", onCloseEnd);
}

// --- Status strip + work state ---
function setWorkItems(items) {
  workState.items = (Array.isArray(items) ? items : []).map(normalizeWorkItem);
  renderWorkItems();
  updateStatusStrip();
}

function upsertWorkItem(item) {
  if (!item?.id) return;
  const existingIndex = workState.items.findIndex((candidate) => candidate.id === item.id);
  const merged = normalizeWorkItem({
    ...(existingIndex >= 0 ? workState.items[existingIndex] : {}),
    ...item,
  });

  if (existingIndex >= 0) {
    workState.items[existingIndex] = merged;
  } else {
    workState.items.push(merged);
  }

  renderWorkItems();
  updateStatusStrip();
}

function normalizeWorkItem(item) {
  const now = Date.now();
  return {
    id: item.id,
    title: item.title || "Untitled work",
    details: item.details || "",
    status: item.status || "active",
    startedAt: item.startedAt || now,
    completedAt: item.completedAt || null,
  };
}

function completeOldestActiveWork() {
  const activeItems = workState.items
    .filter((item) => item.status === "active")
    .sort((a, b) => a.startedAt - b.startedAt);

  if (activeItems.length === 0) return;
  const oldest = activeItems[0];
  upsertWorkItem({
    id: oldest.id,
    status: "completed",
    completedAt: Date.now(),
    details: oldest.details || "Completed",
  });
}

function renderWorkItems() {
  if (!workItemsEl) return;

  const active = workState.items.filter((item) => item.status === "active");
  const needsReview = workState.items.filter((item) => item.status === "needs_review");
  const completed = workState.items.filter((item) => item.status === "completed");

  const rows = [];

  [...active, ...needsReview].forEach((item) => {
    rows.push(workItemTemplate(item));
  });

  if (completed.length > 0) {
    const completedRows = completed
      .slice(0, 8)
      .map((item) => `<div class="work-completed-row">${escapeHtml(item.title)}</div>`)
      .join("");

    rows.push(`
      <div class="expandable" data-expandable>
        <button type="button" class="expand-toggle" data-expand-toggle aria-expanded="false">
          <span class="expand-chevron" aria-hidden="true">\u25b8</span>
          Completed today (${completed.length})
        </button>
        <div class="expand-panel" data-expand-panel hidden>
          <div class="expand-content" data-expand-content>${completedRows}</div>
        </div>
      </div>
    `);
  }

  workItemsEl.innerHTML = rows.join("");
  wireExpandables(workItemsEl);

  if (!elapsedTimer) {
    elapsedTimer = setInterval(updateElapsedTimes, 1000);
  }

  updateElapsedTimes();
  renderSideWorkItems();
  renderSessionInfo();
}

function workItemTemplate(item) {
  const marker =
    item.status === "needs_review"
      ? '<span class="work-item-marker work-item-diamond">\u25c6</span>'
      : '<span class="work-item-marker work-item-dot" aria-hidden="true"></span>';

  const details = item.details ? expandableTemplate("Details", item.details) : "";
  return `
    <article class="work-item-row-block" data-work-id="${escapeHtml(item.id)}">
      <div class="work-item-row">
        ${marker}
        <span class="work-item-title">${escapeHtml(item.title)}</span>
        <span class="work-item-elapsed" data-elapsed-id="${escapeHtml(item.id)}">${elapsedLabel(item)}</span>
      </div>
      ${details}
    </article>
  `;
}

function elapsedLabel(item) {
  const from = item.status === "completed" && item.completedAt ? item.completedAt : Date.now();
  const deltaMs = Math.max(0, from - (item.startedAt || Date.now()));
  const seconds = Math.floor(deltaMs / 1000);
  const mins = Math.floor(seconds / 60);
  const rem = seconds % 60;
  return `${mins}:${rem.toString().padStart(2, "0")}`;
}

function updateElapsedTimes() {
  if (!workItemsEl) return;

  workState.items.forEach((item) => {
    const target = workItemsEl.querySelector(`[data-elapsed-id="${cssEscape(item.id)}"]`);
    if (target) target.textContent = elapsedLabel(item);
  });
}

function renderSideWorkItems() {
  if (!sideWorkItemsEl) return;

  const priority = {
    needs_review: 0,
    active: 1,
    completed: 2,
  };

  const ordered = [...workState.items].sort((a, b) => {
    const priorityDelta = (priority[a.status] || 99) - (priority[b.status] || 99);
    if (priorityDelta !== 0) return priorityDelta;
    return b.startedAt - a.startedAt;
  });

  if (ordered.length === 0) {
    sideWorkItemsEl.innerHTML = `<p class="side-panel-empty">No active work right now.</p>`;
    return;
  }

  sideWorkItemsEl.innerHTML = ordered
    .slice(0, 12)
    .map((item) => {
      return `
        <article class="side-work-row">
          <p class="side-work-title">${escapeHtml(item.title)}</p>
          <p class="side-work-meta">${escapeHtml(sideWorkStatusLabel(item))}</p>
        </article>
      `;
    })
    .join("");
}

function sideWorkStatusLabel(item) {
  if (item.status === "needs_review") return "Needs review";
  if (item.status === "completed") return "Completed";
  return `Active · ${elapsedLabel(item)}`;
}

function renderSessionInfo() {
  if (!sessionInfoEl) return;

  const activeCount = workState.items.filter((item) => item.status === "active").length;
  const needsReviewCount = workState.items.filter((item) => item.status === "needs_review").length;
  const completedCount = workState.items.filter((item) => item.status === "completed").length;

  sessionInfoEl.innerHTML = `
    <dl class="side-session-list">
      <div><dt>Connection</dt><dd>${escapeHtml(connectionState)}</dd></div>
      <div><dt>Messages</dt><dd>${messageCount}</dd></div>
      <div><dt>Active</dt><dd>${activeCount}</dd></div>
      <div><dt>Needs review</dt><dd>${needsReviewCount}</dd></div>
      <div><dt>Completed</dt><dd>${completedCount}</dd></div>
    </dl>
  `;
}

function updateStatusStrip() {
  if (!statusStrip || !workStatus) return;

  const activeCount = workState.items.filter((item) => item.status === "active").length;
  const needsReviewCount = workState.items.filter((item) => item.status === "needs_review").length;

  if (connectionState !== "connected") {
    statusStrip.classList.remove("hidden", "is-clear");
    statusStrip.classList.add("is-visible");
    statusStrip.classList.add("is-banner");
    workStatus.textContent = "Reconnecting…";
    statusStrip.setAttribute("aria-label", "Reconnecting");
    return;
  }

  statusStrip.classList.remove("is-banner");

  if (activeCount === 0 && needsReviewCount === 0) {
    statusStrip.classList.add("hidden", "is-clear");
    statusStrip.classList.remove("is-visible");
    workStatus.textContent = "";
    statusStrip.setAttribute("aria-label", "No active work");
    return;
  }

  const activeLabel = `${activeCount} active`;
  if (needsReviewCount > 0) {
    const reviewLabel = `${needsReviewCount} needs review`;
    workStatus.innerHTML = `${activeLabel} · <span class="status-strip-tint">${reviewLabel}</span>`;
    statusStrip.setAttribute("aria-label", `${activeLabel}, ${reviewLabel}`);
  } else {
    workStatus.textContent = activeLabel;
    statusStrip.setAttribute("aria-label", activeLabel);
  }

  statusStrip.classList.remove("hidden", "is-clear");
  statusStrip.classList.add("is-visible");
}

function openWorkPanel(visible = SHEET_SNAP_VISIBLE.peek) {
  if (!workPanel || !workPanelBackdrop || !workPanelSheet) return;
  if (panelVisiblePercent === 0 && document.activeElement instanceof HTMLElement) {
    workPanelFocusReturn = document.activeElement;
  }

  workPanel.setAttribute("aria-hidden", "false");
  statusStrip?.setAttribute("aria-expanded", "true");

  workPanelBackdrop.hidden = false;
  requestAnimationFrame(() => {
    workPanelBackdrop.classList.add("is-open");
  });

  snapPanel(visible, false);

  const focusDelay = prefersReducedMotion.matches ? 0 : MOTION.fast;
  setTimeout(() => {
    focusFirstElement(workPanelSheet);
  }, focusDelay);
}

function closeWorkPanel() {
  if (!workPanel || !workPanelBackdrop || !workPanelSheet) return;
  snapPanel(SHEET_SNAP_VISIBLE.dismissed, false);
}

function snapPanel(visiblePercent, immediate) {
  const clamped = clamp(visiblePercent, 0, 85);
  panelVisiblePercent = clamped;

  const translatePercent = 100 - clamped;

  if (immediate || prefersReducedMotion.matches) {
    workPanelSheet.style.transition = "none";
  } else {
    workPanelSheet.style.transition = `transform ${MOTION.slow}ms var(--ease-spring)`;
  }

  workPanelSheet.style.transform = `translateY(${translatePercent}%)`;

  if (immediate) {
    void workPanelSheet.offsetHeight;
    workPanelSheet.style.transition = "";
  }

  if (clamped > 0) {
    workPanel.setAttribute("aria-hidden", "false");
    statusStrip?.setAttribute("aria-expanded", "true");
  }

  if (clamped === 0) {
    statusStrip?.setAttribute("aria-expanded", "false");
    workPanel.setAttribute("aria-hidden", "true");
    workPanelBackdrop.classList.remove("is-open");
    const delay = prefersReducedMotion.matches ? 0 : MOTION.fast;
    setTimeout(() => {
      if (panelVisiblePercent === 0) {
        workPanelBackdrop.hidden = true;
      }
    }, delay);

    if (workPanelFocusReturn && document.contains(workPanelFocusReturn)) {
      workPanelFocusReturn.focus();
    } else {
      statusStrip?.focus();
    }
    workPanelFocusReturn = null;
  }
}

function initWorkPanelInteractions() {
  if (!statusStrip || !workPanelHandle || !workPanelSheet) return;

  statusStrip.addEventListener("click", () => {
    openWorkPanel(panelVisiblePercent > 0 ? panelVisiblePercent : SHEET_SNAP_VISIBLE.peek);
  });

  workPanelBackdrop?.addEventListener("click", () => {
    closeWorkPanel();
  });

  let drag = null;

  const onPointerMove = (event) => {
    if (!drag) return;
    const now = performance.now();
    const deltaY = event.clientY - drag.startY;
    const deltaPercent = (deltaY / window.innerHeight) * 100;
    const nextTranslate = clamp(drag.startTranslate + deltaPercent, 15, 100);

    const dt = Math.max(1, now - drag.lastTime);
    drag.velocity = (event.clientY - drag.lastY) / dt;
    drag.lastY = event.clientY;
    drag.lastTime = now;

    workPanelSheet.style.transition = "none";
    workPanelSheet.style.transform = `translateY(${nextTranslate}%)`;
    panelVisiblePercent = 100 - nextTranslate;
  };

  const onPointerUp = () => {
    if (!drag) return;
    workPanelHandle.releasePointerCapture(drag.pointerId);

    const currentTranslate = 100 - panelVisiblePercent;
    const snaps = [100, 60, 15];
    let target = nearestSnap(currentTranslate, snaps);

    if (drag.velocity > 0.45) {
      target = snaps.find((snap) => snap >= currentTranslate + 5) || 100;
    } else if (drag.velocity < -0.45) {
      const reversed = [...snaps].reverse();
      target = reversed.find((snap) => snap <= currentTranslate - 5) || 15;
    }

    snapPanel(100 - target, false);

    document.removeEventListener("pointermove", onPointerMove);
    document.removeEventListener("pointerup", onPointerUp);
    document.removeEventListener("pointercancel", onPointerUp);

    drag = null;
  };

  workPanelHandle.addEventListener("pointerdown", (event) => {
    openWorkPanel(panelVisiblePercent || SHEET_SNAP_VISIBLE.peek);

    drag = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startTranslate: 100 - panelVisiblePercent,
      lastY: event.clientY,
      lastTime: performance.now(),
      velocity: 0,
    };

    workPanelHandle.setPointerCapture(event.pointerId);
    document.addEventListener("pointermove", onPointerMove);
    document.addEventListener("pointerup", onPointerUp);
    document.addEventListener("pointercancel", onPointerUp);
  });
}

function initSidePanel() {
  if (!sidePanel || !sidePanelToggle) return;

  sidePanelToggle.addEventListener("click", () => {
    toggleSidePanel();
  });

  sidePanelClose?.addEventListener("click", () => {
    setSidePanelOpen(false);
  });

  sideTabButtons.forEach((tabButton) => {
    tabButton.addEventListener("click", () => {
      const tab = tabButton.getAttribute("data-side-tab") || "memory";
      activateSidePanelTab(tab);
    });
  });

  DESKTOP_QUERY.addEventListener("change", () => {
    if (!isDesktopLayout()) {
      setSidePanelOpen(false);
    }
  });

  activateSidePanelTab(sidePanelTab);
}

function isDesktopLayout() {
  return DESKTOP_QUERY.matches;
}

function toggleSidePanel() {
  if (!isDesktopLayout()) return;
  setSidePanelOpen(!sidePanelOpen);
}

function setSidePanelOpen(open) {
  if (!sidePanel) return;
  const canOpen = open && isDesktopLayout();
  sidePanelOpen = canOpen;
  sidePanel.classList.toggle("is-open", canOpen);
  sidePanel.setAttribute("aria-hidden", String(!canOpen));
  sidePanelToggle?.setAttribute("aria-expanded", String(canOpen));

  if (canOpen) {
    const focusDelay = prefersReducedMotion.matches ? 0 : MOTION.fast;
    setTimeout(() => {
      focusFirstElement(sidePanel);
    }, focusDelay);
  } else if (sidePanel.contains(document.activeElement)) {
    sidePanelToggle?.focus();
  }
}

function activateSidePanelTab(tab) {
  if (!sideTabPanels[tab]) return;
  sidePanelTab = tab;

  sideTabButtons.forEach((button) => {
    const active = button.getAttribute("data-side-tab") === tab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    button.setAttribute("tabindex", active ? "0" : "-1");
  });

  Object.entries(sideTabPanels).forEach(([panelId, panel]) => {
    if (!panel) return;
    panel.hidden = panelId !== tab;
  });
}

function openShortcutOverlay() {
  if (!shortcutOverlay || !shortcutSheet || shortcutOpen) return;
  shortcutOpen = true;
  shortcutFocusReturn = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  shortcutOverlay.hidden = false;
  requestAnimationFrame(() => {
    shortcutOverlay.classList.add("is-open");
  });

  const focusDelay = prefersReducedMotion.matches ? 0 : MOTION.fast;
  setTimeout(() => {
    focusFirstElement(shortcutSheet);
  }, focusDelay);
}

function closeShortcutOverlay() {
  if (!shortcutOverlay || !shortcutSheet || !shortcutOpen) return;
  shortcutOpen = false;
  shortcutOverlay.classList.remove("is-open");
  const delay = prefersReducedMotion.matches ? 0 : MOTION.fast;
  setTimeout(() => {
    if (!shortcutOpen) {
      shortcutOverlay.hidden = true;
      if (shortcutFocusReturn && document.contains(shortcutFocusReturn)) {
        shortcutFocusReturn.focus();
      }
      shortcutFocusReturn = null;
    }
  }, delay);
}

function focusableElements(scope) {
  if (!scope) return [];
  return [...scope.querySelectorAll("a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])")]
    .filter((el) => !el.hasAttribute("hidden") && el.getAttribute("aria-hidden") !== "true");
}

function focusFirstElement(scope) {
  const focusables = focusableElements(scope);
  if (focusables.length === 0) return;
  focusables[0].focus();
}

function trapFocusWithin(scope, event) {
  if (event.key !== "Tab") return false;
  const focusables = focusableElements(scope);
  if (focusables.length === 0) return false;

  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = document.activeElement;

  if (!scope.contains(active)) {
    event.preventDefault();
    first.focus();
    return true;
  }

  if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
    return true;
  }

  if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
    return true;
  }

  return false;
}

function nearestSnap(value, snaps) {
  return snaps.reduce((closest, snap) => {
    return Math.abs(snap - value) < Math.abs(closest - value) ? snap : closest;
  }, snaps[0]);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function cssEscape(text) {
  if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(text);
  return String(text).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

function readDurationMs(varName, fallback) {
  const raw = getComputedStyle(root).getPropertyValue(varName).trim();
  if (!raw) return fallback;
  if (raw.endsWith("ms")) {
    const value = Number.parseFloat(raw.slice(0, -2));
    return Number.isFinite(value) ? value : fallback;
  }
  if (raw.endsWith("s")) {
    const value = Number.parseFloat(raw.slice(0, -1));
    return Number.isFinite(value) ? value * 1000 : fallback;
  }
  return fallback;
}

// --- Send ---
composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  const slashQuery = slashQueryFromInput();
  if (slashQuery !== null) {
    if (!slashPaletteOpen) openSlashPalette(slashQuery);
    if (slashFilteredCommands.length > 0) {
      runSlashCommand(slashSelectedIndex);
    }
    return;
  }

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    setConnectionStatus("offline");
    scheduleReconnect();
    return;
  }

  addMessage("user", text);
  addThinking();

  const workId = `active-${Date.now()}`;
  upsertWorkItem({
    id: workId,
    title: text.length > 40 ? `${text.slice(0, 37)}...` : text,
    details: "Running request",
    status: "active",
    startedAt: Date.now(),
  });

  ws.send(JSON.stringify({ type: "message", sender_id: "owner", text }));

  input.value = "";
  input.style.height = "auto";
  updateComposerState();
  closeSlashPalette();
});

// --- Keyboard ---
input.addEventListener("keydown", (e) => {
  if (slashPaletteOpen) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveSlashSelection(1);
      return;
    }

    if (e.key === "ArrowUp") {
      e.preventDefault();
      moveSlashSelection(-1);
      return;
    }

    if (e.key === "Escape") {
      e.preventDefault();
      closeSlashPalette();
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (slashFilteredCommands.length > 0) {
        runSlashCommand(slashSelectedIndex);
      }
      return;
    }
  }

  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    composer.requestSubmit();
    return;
  }

  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    composer.requestSubmit();
  }
});

document.addEventListener("keydown", (event) => {
  if (panelVisiblePercent > 0 && trapFocusWithin(workPanelSheet, event)) {
    return;
  }

  if (shortcutOpen && trapFocusWithin(shortcutSheet, event)) {
    return;
  }

  const isEditable = isEditableTarget(event.target);
  const key = event.key.toLowerCase();

  if ((event.metaKey || event.ctrlKey) && key === "k") {
    event.preventDefault();
    focusComposer();
    return;
  }

  if (!isEditable && !event.metaKey && !event.ctrlKey && !event.altKey && event.key === "/") {
    event.preventDefault();
    focusComposer();
    return;
  }

  if ((event.metaKey || event.ctrlKey) && event.key === ".") {
    event.preventDefault();
    toggleSidePanel();
    return;
  }

  if (!isEditable && !event.metaKey && !event.ctrlKey && !event.altKey && event.key === "?") {
    event.preventDefault();
    if (shortcutOpen) closeShortcutOverlay();
    else openShortcutOverlay();
    return;
  }

  if (event.key === "Escape") {
    if (slashPaletteOpen) {
      closeSlashPalette();
      event.preventDefault();
      return;
    }

    if (shortcutOpen) {
      closeShortcutOverlay();
      event.preventDefault();
      return;
    }

    if (sidePanelOpen) {
      setSidePanelOpen(false);
      event.preventDefault();
      return;
    }

    if (panelVisiblePercent > 0) {
      closeWorkPanel();
      event.preventDefault();
      return;
    }

    if (document.activeElement === input) {
      input.blur();
      event.preventDefault();
    }
  }
});

// --- Network ---
window.addEventListener("online", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) connect(true);
});
window.addEventListener("offline", () => setConnectionStatus("offline"));

shortcutOverlay?.addEventListener("click", (event) => {
  if (event.target === shortcutOverlay) {
    closeShortcutOverlay();
  }
});

shortcutClose?.addEventListener("click", () => {
  closeShortcutOverlay();
});

// --- Init ---
connect();
initWorkPanelInteractions();
initSidePanel();
snapPanel(SHEET_SNAP_VISIBLE.dismissed, true);
renderWorkItems();
updateStatusStrip();
updateComposerState();
renderSessionInfo();
setSidePanelOpen(false);
focusComposer();

if (new URLSearchParams(window.location.search).get("demo") === "1" && DEMO_APPROVAL_ITEMS.length > 0) {
  setTimeout(() => {
    renderActionCards(DEMO_APPROVAL_ITEMS);
  }, 1200);
}
