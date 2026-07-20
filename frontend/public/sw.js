// Minimal service worker: provides a fetch handler so the app is installable as
// a PWA on Android Chrome (Add to Home Screen -> standalone window). It is a
// transparent network pass-through — no caching, so content is never stale.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});

// Flyer-day Web Push (P41 A). The server sends at most one notification per
// flyer flip, hard-capped at 2/week — this handler just renders whatever
// arrives and opens the payload's URL on tap.
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    /* non-JSON payload — show a generic notification */
  }
  const title = data.title || "PantryAI";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: data.url || "/" },
      tag: "pantryai-flyer", // replaces, never stacks
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if ("focus" in w) {
          w.navigate(url);
          return w.focus();
        }
      }
      return clients.openWindow(url);
    }),
  );
});
