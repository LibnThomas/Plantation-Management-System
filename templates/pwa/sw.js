/* Plantation Management System - service worker (version {{ version }}).
   Keeps the app's own files (styles, scripts, icons, fonts) on the phone so pages open quickly.
   Pages and data always come from the server, so nobody sees old figures; without a
   connection a friendly "you are offline" page is shown. */
const VERSION = "pms-{{ version }}";
const OFFLINE_URL = "/app/offline/";
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll([OFFLINE_URL])).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/static/")) {  // app files: cache first
    event.respondWith(caches.open(VERSION).then((cache) => cache.match(req).then((hit) => hit || fetch(req).then((resp) => {
      if (resp.ok) cache.put(req, resp.clone());
      return resp;
    }))));
    return;
  }
  if (req.mode === "navigate") {  // pages: always from the server, offline page if there is no connection
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
  }
});
