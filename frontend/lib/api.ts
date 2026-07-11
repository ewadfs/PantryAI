import { createClient } from "./supabase";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/**
 * Typed fetch wrapper for the PantryAI backend.
 *
 * - Attaches the current Supabase session access token as a Bearer header.
 * - On 401, bounces the user to /login (client-side) and throws.
 * - JSON in / JSON out; pass a plain object as `json` and it's serialized.
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: (Omit<RequestInit, "body"> & { json?: unknown; body?: BodyInit }) = {},
): Promise<T> {
  const { json, headers, ...rest } = options;

  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const h = new Headers(headers);
  if (session?.access_token) {
    h.set("Authorization", `Bearer ${session.access_token}`);
  }

  let body = rest.body;
  if (json !== undefined) {
    h.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }

  const res = await fetch(`${API_URL}${path}`, { ...rest, headers: h, body });

  if (res.status === 401) {
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Unauthorized");
  }

  const isJson = res.headers.get("content-type")?.includes("application/json");
  const payload = isJson ? await res.json().catch(() => null) : await res.text();

  if (!res.ok) {
    const message =
      (isJson && payload && (payload.detail || payload.message)) ||
      `Request failed (${res.status})`;
    throw new ApiError(res.status, String(message), payload);
  }

  return payload as T;
}
