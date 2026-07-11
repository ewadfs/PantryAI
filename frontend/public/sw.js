// Minimal service worker: provides a fetch handler so the app is installable as
// a PWA on Android Chrome (Add to Home Screen -> standalone window). It is a
// transparent network pass-through — no caching, so content is never stale.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
