import { apiFetch } from "./api";

export type Deal = {
  id: number;
  product_name: string;
  brand: string | null;
  sale_price: string | number;
  price_unit: string | null;
  regular_price: string | number | null;
  savings_pct: string | number | null;
  deal_type: string | null;
  category: string | null;
  valid_from: string | null;
  valid_to: string | null;
};

export type DealsState = "ready" | "loading" | "pending_source" | "no_store";

export type DealListResponse = {
  count: number;
  page: number;
  per_page: number;
  state: DealsState;
  deals: Deal[];
};

export type DealsStateResponse = {
  state: DealsState;
  chain_name: string | null;
  region_key: string | null;
  // Whether the circular viewer feature is exposed (gates entry links).
  circular_viewer: boolean;
};

export type CircularPage = {
  page_number: number;
  image_url: string;
  deals: Deal[];
};

export type CircularResponse = {
  state: "ready" | "no_images" | "expired" | "no_store";
  chain_name: string | null;
  chain_slug: string | null;
  store_name: string | null;
  valid_from: string | null;
  valid_to: string | null;
  refresh_day: string | null;
  pages: CircularPage[];
  deals: Deal[];
};

export const getCircular = (chain?: string) =>
  apiFetch<CircularResponse>(
    `/api/v1/deals/circular${chain ? `?chain=${encodeURIComponent(chain)}` : ""}`,
  );

export const getDealsState = () =>
  apiFetch<DealsStateResponse>("/api/v1/deals/state");

export const getTopDeals = () => apiFetch<Deal[]>("/api/v1/deals/top");

export function getDeals(params: {
  search?: string;
  category?: string;
  page?: number;
  per_page?: number;
}): Promise<DealListResponse> {
  const qs = new URLSearchParams();
  if (params.search) qs.set("search", params.search);
  if (params.category) qs.set("category", params.category);
  qs.set("page", String(params.page ?? 1));
  qs.set("per_page", String(params.per_page ?? 20));
  return apiFetch<DealListResponse>(`/api/v1/deals?${qs.toString()}`);
}

// P42 B: the Home week-ahead surface — flyer-flip ritual numbers.
export type RitualResponse = {
  is_flip_day: boolean;
  store_name: string | null;
  chain_name: string | null;
  deal_count: number;
  pantry_matches: number;
  expiring_count: number;
  flipped_at: string | null;
  valid_to: string | null;
};

export const getRitual = () => apiFetch<RitualResponse>("/api/v1/deals/ritual");
