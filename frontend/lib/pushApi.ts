import { apiFetch } from "./api";

/** Web Push subscribe/unsubscribe plumbing (P41 A). */

export type PushStatus = { enabled: boolean; subscribed_endpoints: string[] };

export const getPushStatus = () => apiFetch<PushStatus>("/api/v1/push/status");

function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const b64 = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** Ask permission, subscribe the browser, register server-side. */
export async function enablePush(): Promise<void> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    throw new Error("This browser doesn't support notifications.");
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") {
    throw new Error("Notifications were not allowed.");
  }
  const { key } = await apiFetch<{ key: string }>("/api/v1/push/public-key");
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(key),
  });
  await apiFetch("/api/v1/push/subscribe", {
    method: "POST",
    json: sub.toJSON(),
  });
}

/** Unsubscribe browser-side AND server-side (server honors immediately). */
export async function disablePush(): Promise<void> {
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (sub) {
    await apiFetch("/api/v1/push/unsubscribe", {
      method: "POST",
      json: { endpoint: sub.endpoint },
    }).catch(() => {});
    await sub.unsubscribe();
  }
}

/** Is THIS browser currently subscribed? */
export async function isPushSubscribed(): Promise<boolean> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;
  try {
    const reg = await navigator.serviceWorker.ready;
    return (await reg.pushManager.getSubscription()) !== null;
  } catch {
    return false;
  }
}
