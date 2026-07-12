import { apiFetch } from "./api";

export type UserProfile = {
  id: number;
  supabase_user_id: string;
  email: string | null;
  name: string | null;
  zip_code: string | null;
  goal: string;
  calorie_target: number;
  protein_target: number;
  diet_type: string;
  allergies: string[];
  cuisine_preferences: string[];
  excluded_ingredients: string[];
  skill_level: string;
  max_prep_time: number;
  household_size: number;
  taste_notes: string | null;
  recipes_per_generation: number;
  created_at?: string;
};

export type UserUpdate = Partial<{
  name: string | null;
  zip_code: string | null;
  goal: string;
  calorie_target: number;
  protein_target: number;
  diet_type: string;
  allergies: string[];
  cuisine_preferences: string[];
  excluded_ingredients: string[];
  skill_level: string;
  max_prep_time: number;
  household_size: number;
  taste_notes: string | null;
  recipes_per_generation: 3 | 5;
}>;

export const getMe = () => apiFetch<UserProfile>("/api/v1/me");

export const updateMe = (patch: UserUpdate) =>
  apiFetch<UserProfile>("/api/v1/me", { method: "PATCH", json: patch });

export function firstName(p: Pick<UserProfile, "name" | "email"> | null): string {
  if (p?.name?.trim()) return p.name.trim().split(/\s+/)[0];
  if (p?.email) return p.email.split("@")[0];
  return "there";
}
