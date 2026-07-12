import { apiFetch } from "./api";

export type SavingsBucket = {
  deal_savings: string | number;
  pantry_value_used: string | number;
  trips: number;
  items: number;
};

export type LastTrip = {
  date: string | null;
  store: string | null;
  deal_savings: string | number;
  known_cost: string | number;
};

export type SavingsResponse = {
  all_time: SavingsBucket;
  this_month: SavingsBucket;
  last_trip: LastTrip | null;
  cooked_recipe_count: number;
};

export const getSavings = () => apiFetch<SavingsResponse>("/api/v1/stats/savings");
