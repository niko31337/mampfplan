// Service Worker: hält Seiten und Assets für den Offline-Betrieb vor.
// - /static/*      : sofort aus dem Cache, im Hintergrund aktualisieren
// - Seiten (/s/…)  : erst Netz (mit Timeout), bei Ausfall die zuletzt gesehene Fassung
// POST-Anfragen fasst er nicht an; Offline-Aktionen puffert app.js.
const VERSION = "v1";
const STATIC_CACHE = `mampfplan-static-${VERSION}`;
const PAGE_CACHE = `mampfplan-pages-${VERSION}`;
const PRECACHE = [
  "/static/style.css",
  "/static/app.js",
  "/static/htmx.min.js",
  "/static/sortable.min.js",
  "/static/icon.svg",
  "/offline",
];
const NETWORK_TIMEOUT_MS = 5000;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => ![STATIC_CACHE, PAGE_CACHE].includes(key)).map((key) => caches.delete(key)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(staleWhileRevalidate(request));
  } else if (url.pathname.startsWith("/s/") && isCacheablePage(request, url)) {
    event.respondWith(networkFirst(request));
  }
});

// Vollständige Seiten: normale Navigation, HTMX-Boost oder gezieltes Vorladen. Teil-Antworten (Fragmente) nicht.
function isCacheablePage(request, url) {
  if (url.pathname.includes("/search") || url.pathname.endsWith("/who")) return false;
  return (
    request.mode === "navigate" ||
    request.headers.get("HX-Boosted") === "true" ||
    request.headers.get("X-Mampf-Prefetch") === "1"
  );
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request.url);
  const refresh = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request.url, response.clone());
      return response;
    })
    .catch(() => cached);
  return cached || refresh;
}

async function networkFirst(request) {
  const cache = await caches.open(PAGE_CACHE);
  try {
    const response = await fetchWithTimeout(request, NETWORK_TIMEOUT_MS);
    const isHtml = (response.headers.get("content-type") || "").includes("text/html");
    if (response.ok && isHtml && !response.redirected) {
      await cache.put(request.url, response.clone());
    }
    return response;
  } catch {
    const cached = await cache.match(request.url);
    if (cached) return cached;
    if (request.mode === "navigate") {
      const offline = await caches.match("/offline");
      if (offline) return offline;
    }
    return new Response("Offline – diese Seite ist nicht zwischengespeichert.", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}

function fetchWithTimeout(request, ms) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return fetch(request, { signal: controller.signal }).finally(() => clearTimeout(timer));
}
