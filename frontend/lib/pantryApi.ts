import { apiFetch } from "./api";
import { createClient } from "./supabase";
import type {
  ConfirmRequest,
  ConfirmResponse,
  PantryItem,
  PantryListResponse,
  ScanResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

/**
 * Upload photos for scanning. Uses XHR (not fetch) so we can report real upload
 * progress for the multipart body; the server then runs Claude vision.
 */
export function scanPantry(
  files: File[],
  onUploadProgress?: (fraction: number) => void,
): Promise<ScanResponse> {
  return new Promise((resolve, reject) => {
    (async () => {
      const supabase = createClient();
      const {
        data: { session },
      } = await supabase.auth.getSession();

      const form = new FormData();
      files.forEach((f) => form.append("files", f, f.name));

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_URL}/api/v1/pantry/scan`);
      if (session?.access_token) {
        xhr.setRequestHeader("Authorization", `Bearer ${session.access_token}`);
      }
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onUploadProgress?.(e.loaded / e.total);
      };
      xhr.onload = () => {
        if (xhr.status === 401) {
          if (typeof window !== "undefined") window.location.href = "/login";
          reject(new Error("Unauthorized"));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            reject(new Error("Malformed scan response"));
          }
        } else {
          let detail = `Scan failed (${xhr.status})`;
          try {
            detail = JSON.parse(xhr.responseText).detail || detail;
          } catch {
            /* ignore */
          }
          reject(new Error(detail));
        }
      };
      xhr.onerror = () => reject(new Error("Network error during upload"));
      xhr.send(form);
    })().catch(reject);
  });
}

export function confirmScan(
  scanId: number,
  payload: ConfirmRequest,
): Promise<ConfirmResponse> {
  return apiFetch<ConfirmResponse>(`/api/v1/pantry/scan/${scanId}/confirm`, {
    method: "POST",
    json: payload,
  });
}

export function listPantry(): Promise<PantryListResponse> {
  return apiFetch<PantryListResponse>("/api/v1/pantry");
}

export function addPantryItem(item: {
  name: string;
  quantity_estimate?: string | null;
  unit?: string | null;
  category?: string | null;
}): Promise<PantryItem> {
  return apiFetch<PantryItem>("/api/v1/pantry/items", {
    method: "POST",
    json: item,
  });
}

export function updatePantryItem(
  id: number,
  patch: Record<string, unknown>,
): Promise<PantryItem> {
  return apiFetch<PantryItem>(`/api/v1/pantry/items/${id}`, {
    method: "PATCH",
    json: patch,
  });
}

export function deletePantryItem(id: number): Promise<{ status: string; id: number }> {
  return apiFetch(`/api/v1/pantry/items/${id}`, { method: "DELETE" });
}
