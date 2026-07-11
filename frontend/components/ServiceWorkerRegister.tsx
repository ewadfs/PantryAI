"use client";

import { useEffect } from "react";

/** Registers the minimal service worker so the app is installable as a PWA. */
export default function ServiceWorkerRegister() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        /* installability is best-effort; ignore registration failures */
      });
    }
  }, []);
  return null;
}
