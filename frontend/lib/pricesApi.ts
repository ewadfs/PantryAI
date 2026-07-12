import { apiFetch } from "./api";

export type MatchedDeal = {
  ingredient: string;
  sale_price: string | number;
  regular_price: string | number | null;
};

export type PriceStore = {
  store_id: number;
  store_name: string | null;
  chain_name: string | null;
  is_default: boolean;
  known_cost_sum: string | number;
  priced_count: number;
  total_count: number;
  unpriced_count: number;
  matched_deals: MatchedDeal[];
};

export type PriceCompareResponse = {
  needed_count: number;
  stores: PriceStore[];
};

export const comparePricesForRecipe = (recipeId: number) =>
  apiFetch<PriceCompareResponse>(`/api/v1/prices/compare?recipe_id=${recipeId}`);

export const comparePricesForList = (listId: number) =>
  apiFetch<PriceCompareResponse>(`/api/v1/prices/compare?list_id=${listId}`);
