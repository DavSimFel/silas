// Silas PWA — Quiet Design Phase B

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
const root = document.documentElement;
const stream = document.getElementById("stream");
const messages = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");
const composer = document.getElementById("composer");
const input = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const statusStrip = document.getElementById("status-strip");
const workStatus = document.getElementById("work-status");
const workPanel = document.getElementById("work-panel");
const workPanelBackdrop = document.getElementById("work-panel-backdrop");
const workPanelSheet = document.getElementById("work-panel-sheet");
const workPanelHandle = document.getElementById("work-panel-handle");
const workItemsEl = document.getElementById("work-items");

const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

let messageCount = 0;
let elapsedTimer = null;
let connectionState = "connecting";
let panelVisiblePercent = 0;

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

// --- Auto-resize textarea ---
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;

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
  connectionState = state;
  const colors = {
    connected: "bg-status-green",
    connecting: "bg-status-amber",
    offline: "bg-status-red",
  };

  statusDot.className = "w-2 h-2 rounded-full transition-colors duration-200";
  statusDot.classList.add(colors[state] || colors.offline);
  updateStatusStrip();
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  setConnectionStatus("connecting");
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${window.location.host}/ws`);

  ws.addEventListener("open", () => {
    reconnectAttempt = 0;
    setConnectionStatus("connected");
  });

  ws.addEventListener("message", (event) => {
    removeThinking();
    try {
      const data = JSON.parse(event.data);
      if (data.type === "message") {
        addMessage("agent", data.text ?? "");
        completeOldestActiveWork();
        return;
      }

      if (data.type === "approval_card" || data.type === "action_card") {
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
  messageCount += 1;

  const el = document.createElement("div");
  el.className = "msg-enter";

  if (role === "user") {
    el.innerHTML = `
      <div class="flex justify-end items-end gap-2">
        <p class="text-[13px] leading-[18px] text-text-secondary max-w-[85%] text-right">${escapeHtml(text)}</p>
        <span class="text-xs text-text-tertiary shrink-0">${timeLabel()}</span>
      </div>
    `;
  } else if (role === "agent") {
    el.innerHTML = `
      <div class="max-w-full">
        <div class="text-[15px] leading-[22px] text-text-primary whitespace-pre-wrap">${escapeHtml(text)}</div>
      </div>
    `;
  } else {
    el.innerHTML = `<p class="text-xs text-text-tertiary text-center">${escapeHtml(text)}</p>`;
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
    <span class="thinking-dot w-2 h-2 rounded-full"></span>
    <span class="thinking-dot w-2 h-2 rounded-full"></span>
    <span class="thinking-dot w-2 h-2 rounded-full"></span>
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

    messages.appendChild(wrapper);

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

  const confirmation =
    (action === "approve" ? card.confirmation?.approve : card.confirmation?.decline) ||
    (action === "approve" ? "\u2713 Approved" : "Declined");

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

function updateStatusStrip() {
  if (!statusStrip || !workStatus) return;

  const activeCount = workState.items.filter((item) => item.status === "active").length;
  const needsReviewCount = workState.items.filter((item) => item.status === "needs_review").length;

  if (connectionState !== "connected") {
    statusStrip.classList.remove("hidden", "is-clear");
    statusStrip.classList.add("is-visible");
    workStatus.innerHTML = `\u25cf ${connectionState === "offline" ? "Connection lost" : "Connecting\u2026"}`;
    return;
  }

  if (activeCount === 0 && needsReviewCount === 0) {
    statusStrip.classList.add("hidden", "is-clear");
    statusStrip.classList.remove("is-visible");
    workStatus.textContent = "";
    return;
  }

  const reviewLabel = `${needsReviewCount} needs review`;
  const activeLabel = `${activeCount} active`;
  workStatus.innerHTML = `${activeLabel} · <span class="status-strip-tint">${reviewLabel}</span>`;

  statusStrip.classList.remove("hidden", "is-clear");
  statusStrip.classList.add("is-visible");
}

function openWorkPanel(visible = SHEET_SNAP_VISIBLE.peek) {
  if (!workPanel || !workPanelBackdrop || !workPanelSheet) return;

  workPanel.setAttribute("aria-hidden", "false");
  workPanel.style.visibility = "visible";
  statusStrip?.setAttribute("aria-expanded", "true");

  workPanelBackdrop.hidden = false;
  requestAnimationFrame(() => {
    workPanelBackdrop.classList.add("is-open");
  });

  snapPanel(visible, false);
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

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && panelVisiblePercent > 0) {
      closeWorkPanel();
    }
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

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addMessage("system", "Reconnecting…");
    connect();
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

initWorkPanelInteractions();
snapPanel(SHEET_SNAP_VISIBLE.dismissed, true);
renderWorkItems();
updateStatusStrip();

if (new URLSearchParams(window.location.search).get("demo") === "1" && DEMO_APPROVAL_ITEMS.length > 0) {
  setTimeout(() => {
    renderActionCards(DEMO_APPROVAL_ITEMS);
  }, 1200);
}
