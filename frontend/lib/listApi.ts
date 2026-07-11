import { ApiError, apiFetch } from "./api";
import type {
  CompleteResponse,
  CurrentList,
  ShoppingItem,
  UserStore,
} from "./listTypes";

/** Latest active list, or null if there is no active list (404). */
export async function getCurrentList(): Promise<CurrentList | null> {
  try {
    return await apiFetch<CurrentList>("/api/v1/lists/current");
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export const getMyStores = () => apiFetch<UserStore[]>("/api/v1/stores/mine");

export const addListItem = (
  listId: number,
  body: { display_name: string; quantity?: string | null; unit?: string | null; notes?: string | null },
) =>
  apiFetch<ShoppingItem>(`/api/v1/lists/${listId}/items`, {
    method: "POST",
    json: body,
  });

export const setItemChecked = (listId: number, itemId: number, is_checked: boolean) =>
  apiFetch<ShoppingItem>(`/api/v1/lists/${listId}/items/${itemId}`, {
    method: "PATCH",
    json: { is_checked },
  });

export const deleteListItem = (listId: number, itemId: number) =>
  apiFetch<{ status: string; id: number }>(
    `/api/v1/lists/${listId}/items/${itemId}`,
    { method: "DELETE" },
  );

export const completeList = (listId: number) =>
  apiFetch<CompleteResponse>(`/api/v1/lists/${listId}/complete`, { method: "POST" });
