// API shapes mirrored from the backend (app/schemas/pantry.py).

export type ScanItem = {
  name: string;
  quantity_estimate: string | null;
  unit: string | null;
  category: string | null;
  freshness: string;
  confidence: number;
  ingredient_id: number | null;
  match_confidence: number;
  estimated_expiry: string | null;
};

export type ScanResponse = {
  scan_id: number;
  items: ScanItem[];
  uncertain: string[];
  photo_count: number;
};

export type ConfirmItem = {
  name: string;
  quantity_estimate?: string | null;
  unit?: string | null;
  category?: string | null;
  is_staple?: boolean;
};

export type ConfirmRequest = {
  mode: "replace" | "merge";
  confirmed: ConfirmItem[];
  removed?: string[];
  corrections?: { ai_said: string; user_said: string }[];
};

export type ConfirmResponse = {
  scan_id: number;
  confirmed: number;
  removed: number;
  active_items: number;
};

export type PantryItem = {
  id: number;
  name: string | null;
  quantity_estimate: string | null;
  unit: string | null;
  category: string | null;
  brand: string | null;
  freshness: string;
  estimated_expiry: string | null;
  is_staple: boolean;
  source: string | null;
  is_active: boolean;
  use_soon: boolean;
};

export type PantryCategoryGroup = {
  category: string;
  items: PantryItem[];
};

export type PantryListResponse = {
  count: number;
  categories: PantryCategoryGroup[];
};
