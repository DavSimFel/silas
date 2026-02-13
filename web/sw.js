const CACHE_VERSION = "silas-v3";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const API_CACHE = `${CACHE_VERSION}-api`;
const PRECACHE_ASSETS = ["/", "/index.html", "/style.css", "/app.js", "/manifest.json"];

function isStaticAsset(url) {
  if (url.origin !== self.location.origin) return false;
  if (url.pathname.startsWith("/icons/")) return true;
  return (
    url.pathname === "/" ||
    url.pathname.endsWith(".html") ||
    url.pathname.endsWith(".css") ||
    url.pathname.endsWith(".js") ||
    url.pathname.endsWith(".json") ||
    url.pathname.endsWith(".png") ||
    url.pathname.endsWith(".svg")
  );
}

function isApiRequest(url) {
  return url.origin === self.location.origin && url.pathname.startsWith("/api/");
}

async function cacheFirstStatic(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response && response.ok) {
    const cache = await caches.open(STATIC_CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}

async function networkFirstApi(request) {
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      const cache = await caches.open(API_CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch (_) {
    const cached = await caches.match(request);
    if (cached) return cached;
    return new Response(
      JSON.stringify({ error: "offline", detail: "No cached API response available." }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
        },
      },
    );
  }
}

function buildNotificationUrl(data, action) {
  const rawUrl = typeof data?.url === "string" && data.url ? data.url : "/";
  const target = new URL(rawUrl, self.location.origin);
  const cardId = typeof data?.card_id === "string" ? data.card_id : "";
  if (cardId && !target.searchParams.has("approval")) {
    target.searchParams.set("approval", cardId);
  }
  if (action && action !== "open") {
    target.searchParams.set("notification_action", action);
  }
  return target.href;
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_ASSETS)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => !key.startsWith(CACHE_VERSION))
          .map((key) => caches.delete(key)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET" || request.url.includes("/ws")) return;

  const url = new URL(request.url);

  if (isApiRequest(url)) {
    event.respondWith(networkFirstApi(request));
    return;
  }

  if (isStaticAsset(url)) {
    event.respondWith(cacheFirstStatic(request));
    return;
  }

  event.respondWith(fetch(request).catch(() => caches.match(request)));
});

self.addEventListener("push", (event) => {
  let payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch (_) {
      payload = { body: event.data.text() };
    }
  }

  const data = payload && typeof payload.data === "object" && payload.data ? payload.data : {};
  const title = typeof payload.title === "string" && payload.title ? payload.title : "Silas";
  const body = typeof payload.body === "string" ? payload.body : "Action needed in Silas.";
  const actions = Array.isArray(payload.actions) && payload.actions.length > 0
    ? payload.actions
    : [
      { action: "approve", title: "Approve" },
      { action: "deny", title: "Deny" },
      { action: "open", title: "Open" },
    ];
  const tag = typeof payload.tag === "string" && payload.tag
    ? payload.tag
    : (typeof data.card_id === "string" && data.card_id ? `approval:${data.card_id}` : "silas:push");

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      tag,
      renotify: true,
      actions,
      data,
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const action = event.action || "open";
  const data = event.notification.data || {};
  const targetUrl = buildNotificationUrl(data, action);

  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const existing = windows.length > 0 ? windows[0] : null;
    if (existing) {
      await existing.focus();
      if ("navigate" in existing) {
        try {
          await existing.navigate(targetUrl);
        } catch (_) {
          // Ignore navigation errors for cross-origin or closed clients.
        }
      }
      existing.postMessage({
        type: "notification_action",
        action,
        card_id: typeof data.card_id === "string" ? data.card_id : "",
        session_id: typeof data.session_id === "string" ? data.session_id : "",
        url: targetUrl,
      });
      return;
    }

    const opened = await self.clients.openWindow(targetUrl);
    if (opened) {
      opened.postMessage({
        type: "notification_action",
        action,
        card_id: typeof data.card_id === "string" ? data.card_id : "",
        session_id: typeof data.session_id === "string" ? data.session_id : "",
        url: targetUrl,
      });
    }
  })());
});
