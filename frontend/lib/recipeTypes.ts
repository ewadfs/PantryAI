export type RecipeIngredient = {
  name: string;
  generic_name: string | null;
  brand: string | null;
  quantity: string | number | null;
  unit: string | null;
  in_pantry: boolean | "partial";
  pantry_quantity: string | null;
  shortfall_quantity: string | null;
  on_sale: boolean;
  sale_store: string | null;
  sale_price: string | number | null;
};

export type KeyIngredient = {
  generic_name: string;
  brand: string | null;
  in_pantry: boolean;
  on_sale: boolean;
  sale_store: string | null;
  sale_price: string | number | null;
};

export type Nutrition = {
  calories: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  // 'calculated' = deterministic USDA compute; 'est' = model estimate; null on
  // concept-stage rows. coverage present only when calculated.
  source?: "calculated" | "est" | null;
  coverage?: number | null;
};

export type RecipeCost = {
  known_buy_cost: string | number;
  unknown_priced_items: number;
  pantry_items_used: number;
};

export type MarketAnchor = {
  name: string;
  sale_price: string | number | null;
  price_unit: string | null;
  savings_pct: number | null;
  store: string | null;
};

export type Recipe = {
  id: number;
  status: string;
  title: string;
  description: string | null;
  difficulty: string | null;
  prep_time_min: number | null;
  cook_time_min: number | null;
  total_time_min: number | null;
  servings: number | null;
  why_this_recipe: string | null;
  key_ingredients: KeyIngredient[];
  ingredients: RecipeIngredient[];
  instructions: string[];
  nutrition_per_serving: Nutrition | null;
  tags: string[] | null;
  cuisine: string | null;
  rating: number | null;
  generated_at: string | null;
  cost: RecipeCost;
  is_market_pick: boolean;
  market_anchor: MarketAnchor | null;
};

export type GenerateResponse = { recipes: Recipe[] };

export type LatestResponse = {
  generated_at: string | null;
  store_name: string | null;
  pinned: string[];
  direction: string | null;
  difficulties: string[];
  recipes: Recipe[];
};

export type WeekRecipe = {
  week_start: string;
  is_cooked: boolean;
  cooked_at: string | null;
  recipe: Recipe;
};

export type WeekResponse = { week_start: string; recipes: WeekRecipe[] };

export type CookedResponse = {
  week_start: string;
  recipe_id: number;
  is_cooked: boolean;
  pantry_items_consumed: string[];
};
