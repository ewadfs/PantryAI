import { apiFetch } from "./api";
import type { StoreLocation, UserStore } from "./listTypes";

export type DiscoveredStore = {
  id: number;
  store_name: string | null;
  address: string | null;
  city: string | null;
  state: string | null;
  zip_code: string | null;
  chain_id: number;
  chain_name: string;
  chain_slug: string;
  distance_miles: number | null;
  has_deals_source: boolean;
  deals_status: string;
};

export type DiscoverResponse = {
  zip_code: string;
  source: "places" | "catalog";
  stores: DiscoveredStore[];
};

export const discoverStores = (zip: string) =>
  apiFetch<DiscoverResponse>(`/api/v1/stores/discover?zip=${encodeURIComponent(zip)}`);

export const getAllStores = () => apiFetch<StoreLocation[]>("/api/v1/stores");

export const getMyStores = () => apiFetch<UserStore[]>("/api/v1/stores/mine");

export const setDefaultStore = (storeLocationId: number) =>
  apiFetch<UserStore[]>(`/api/v1/stores/mine/default/${storeLocationId}`, {
    method: "PUT",
  });

export const replaceMyStores = (
  store_location_ids: number[],
  default_store_id: number | null,
) =>
  apiFetch<UserStore[]>("/api/v1/stores/mine", {
    method: "PUT",
    json: { store_location_ids, default_store_id },
  });
