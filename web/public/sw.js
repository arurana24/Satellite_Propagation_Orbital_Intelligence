/* Service worker: makes the site work offline after the first visit.
   - Pyodide runtime + wheels from the pinned CDN path are immutable -> cache-first.
   - Hashed build assets (/assets/*) are immutable -> cache-first.
   - index.html, engine bundle, map data -> network-first, falling back to cache when offline. */
const CACHE = "oi-v1";  // bump to drop old caches
const CDN = "https://cdn.jsdelivr.net/pyodide/";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (ev) => {
  ev.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

// The first page load happens before this worker controls the page; the page sends us the URLs it
// already loaded so they can be cached too.
self.addEventListener("message", (ev) => {
  if (ev.data?.type !== "cache-urls") return;
  ev.waitUntil((async () => {
    const c = await caches.open(CACHE);
    for (const u of ev.data.urls) {
      try {
        if (await c.match(u, { ignoreVary: true })) continue;
        const r = await fetch(u, { mode: u.startsWith(self.location.origin) ? "same-origin" : "cors" });
        if (r.ok) await c.put(u, r);
      } catch (_) { /* best effort */ }
    }
    ev.source?.postMessage({ type: "cached", n: ev.data.urls.length });
  })());
});

const cacheFirst = async (req) => {
  const c = await caches.open(CACHE);
  const hit = await c.match(req, { ignoreVary: true });
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok) c.put(req, r.clone());
  return r;
};

const networkFirst = async (req) => {
  const c = await caches.open(CACHE);
  try {
    const r = await fetch(req);
    if (r.ok) c.put(req, r.clone());
    return r;
  } catch (e) {
    const hit = await c.match(req, { ignoreSearch: true, ignoreVary: true });
    if (hit) return hit;
    if (req.mode === "navigate") { const idx = await c.match("/", { ignoreVary: true }); if (idx) return idx; }
    throw e;
  }
};

self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.href.startsWith(CDN)) { ev.respondWith(cacheFirst(req)); return; }
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/assets/")) { ev.respondWith(cacheFirst(req)); return; }
  ev.respondWith(networkFirst(req));
});
