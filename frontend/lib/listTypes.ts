export type FromRecipe = {
  recipe_id: number;
  title: string;
  qty: string | number | null;
  unit: string | null;
};

export type ShoppingItem = {
  id: number;
  ingredient_id: number | null;
  display_name: string | null;
  quantity: string | null;
  unit: string | null;
  category: string | null;
  price: string | number | null;
  is_on_sale: boolean;
  regular_price: string | number | null;
  deal_id: number | null;
  from_recipes: FromRecipe[] | null;
  is_checked: boolean;
  is_manual_add: boolean;
  notes: string | null;
};

export type ShoppingCategoryGroup = {
  category: string;
  items: ShoppingItem[];
};

export type AlsoOnSale = {
  deal_id: number;
  product_name: string;
  sale_price: string | number;
  regular_price: string | number | null;
  savings_pct: string | number | null;
  price_unit: string | null;
};

export type CurrentList = {
  id: number;
  week_start: string | null;
  status: string;
  total_known_cost: string | number | null;
  deal_savings: string | number | null;
  item_count: number | null;
  categories: ShoppingCategoryGroup[];
  also_on_sale: AlsoOnSale[];
};

export type CompleteResponse = { items_added_to_pantry: number };

export type StoreLocation = {
  id: number;
  store_name: string | null;
  chain_name: string | null;
  chain_slug: string | null;
};

export type UserStore = { is_default: boolean; store: StoreLocation };
