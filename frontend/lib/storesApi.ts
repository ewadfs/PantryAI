import { apiFetch } from "./api";
import type { StoreLocation, UserStore } from "./listTypes";

export const getAllStores = () => apiFetch<StoreLocation[]>("/api/v1/stores");

export const getMyStores = () => apiFetch<UserStore[]>("/api/v1/stores/mine");

export const replaceMyStores = (
  store_location_ids: number[],
  default_store_id: number | null,
) =>
  apiFetch<UserStore[]>("/api/v1/stores/mine", {
    method: "PUT",
    json: { store_location_ids, default_store_id },
  });
