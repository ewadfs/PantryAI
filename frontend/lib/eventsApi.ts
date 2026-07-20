import { apiFetch } from "./api";

/**
 * Fire-and-forget product-event reporting (P40 C6). The backend allowlists
 * which events a client may claim; anything else 400s. Never awaited by UI
 * flows and never throws — instrumentation must not break the product.
 */
export function reportEvent(event: string, meta?: Record<string, unknown>): void {
  apiFetch("/api/v1/events", { method: "POST", json: { event, meta } }).catch(
    () => {
      /* best-effort */
    },
  );
}
