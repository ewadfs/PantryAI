import { apiFetch } from "./api";
import type {
  CookedResponse,
  GenerateResponse,
  LatestResponse,
  Recipe,
  WeekRecipe,
  WeekResponse,
} from "./recipeTypes";

export type Difficulty = "easy" | "medium" | "hard";

export const generateRecipes = (
  pinnedIds: number[] = [],
  direction?: string | null,
  difficulties: Difficulty[] = [],
  pinnedDealIds: number[] = [],
  pantryMode = false,
) =>
  apiFetch<GenerateResponse>("/api/v1/recipes/generate", {
    method: "POST",
    json: {
      pinned_pantry_item_ids: pinnedIds,
      ...(pinnedDealIds.length ? { pinned_deal_ids: pinnedDealIds } : {}),
      ...(direction && direction.trim() ? { direction: direction.trim() } : {}),
      // Omit when all three selected — server treats empty as "all".
      ...(difficulties.length && difficulties.length < 3 ? { difficulties } : {}),
      ...(pantryMode ? { pantry_mode: true } : {}),
    },
  });

export const getLatestRecipes = () =>
  apiFetch<LatestResponse>("/api/v1/recipes/latest");

export const getRecipe = (id: number) => apiFetch<Recipe>(`/api/v1/recipes/${id}`);

export const rateRecipe = (id: number, rating: 1 | -1) =>
  apiFetch<Recipe>(`/api/v1/recipes/${id}/rate`, { method: "POST", json: { rating } });

export const saveToWeek = (id: number, week_start: string) =>
  apiFetch<WeekRecipe>(`/api/v1/recipes/${id}/save-to-week`, {
    method: "POST",
    json: { week_start },
  });

export const getWeek = (week: string) =>
  apiFetch<WeekResponse>(`/api/v1/week/${week}`);

export const removeFromWeek = (week: string, id: number) =>
  apiFetch<{ status: string; recipe_id: number }>(
    `/api/v1/week/${week}/recipes/${id}`,
    { method: "DELETE" },
  );

export const markCooked = (week: string, id: number) =>
  apiFetch<CookedResponse>(`/api/v1/week/${week}/recipes/${id}/cooked`, {
    method: "POST",
  });

export const buildShoppingList = (week_start: string) =>
  apiFetch<{ id: number; item_count: number }>(`/api/v1/lists/build`, {
    method: "POST",
    json: { week_start },
  });

// Public sharing (P41 B): opt-in, revocable.
export const shareRecipe = (id: number) =>
  apiFetch<{ slug: string; url: string }>(`/api/v1/recipes/${id}/share`, {
    method: "POST",
  });

export const unshareRecipe = (id: number) =>
  apiFetch<{ status: string }>(`/api/v1/recipes/${id}/share`, {
    method: "DELETE",
  });
