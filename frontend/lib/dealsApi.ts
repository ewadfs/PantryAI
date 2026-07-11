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

export type DealListResponse = {
  count: number;
  page: number;
  per_page: number;
  deals: Deal[];
};

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
