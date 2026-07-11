// Static reference data — no API needed. Used for grouping and for the
// "+ Add item" suggestion box.

export const CATEGORIES = [
  "produce",
  "meat",
  "seafood",
  "dairy",
  "frozen",
  "bakery",
  "deli",
  "snacks",
  "beverages",
  "pantry",
  "condiments",
  "household",
  "other",
] as const;

export type Category = (typeof CATEGORIES)[number];

export const CATEGORY_LABELS: Record<string, string> = {
  produce: "Produce",
  meat: "Meat",
  seafood: "Seafood",
  dairy: "Dairy",
  frozen: "Frozen",
  bakery: "Bakery",
  deli: "Deli",
  snacks: "Snacks",
  beverages: "Beverages",
  pantry: "Pantry",
  condiments: "Condiments",
  household: "Household",
  staples: "Staples",
  other: "Other",
};

export function categoryLabel(c: string | null | undefined): string {
  if (!c) return "Other";
  return CATEGORY_LABELS[c] ?? c.charAt(0).toUpperCase() + c.slice(1);
}

// Guess a category from a free-text item name (best-effort, for manual adds).
const KEYWORD_CATEGORY: [RegExp, Category][] = [
  [/milk|cheese|yogurt|butter|egg|cream/i, "dairy"],
  [/chicken|beef|pork|turkey|steak|bacon|sausage|ground/i, "meat"],
  [/shrimp|salmon|tuna|fish|cod|crab|tilapia/i, "seafood"],
  [/apple|banana|onion|garlic|tomato|lettuce|pepper|potato|carrot|spinach|broccoli|lemon|lime|berr|grape|avocado/i, "produce"],
  [/bread|bagel|tortilla|bun|roll|muffin/i, "bakery"],
  [/frozen|ice cream|pizza/i, "frozen"],
  [/chip|cracker|cookie|popcorn|pretzel|candy|nut/i, "snacks"],
  [/water|juice|soda|coffee|tea|cola|sprite|milk/i, "beverages"],
  [/rice|pasta|flour|sugar|bean|oat|cereal|broth|sauce|oil|vinegar|spice|salt/i, "pantry"],
  [/ketchup|mustard|mayo|dressing|salsa|soy sauce|hot sauce/i, "condiments"],
  [/paper|soap|detergent|trash|towel|foil|wrap/i, "household"],
];

export function guessCategory(name: string): Category {
  for (const [re, cat] of KEYWORD_CATEGORY) if (re.test(name)) return cat;
  return "other";
}

// Common grocery items for the debounced suggestion box.
export const COMMON_ITEMS = [
  "Milk", "Eggs", "Butter", "Cheddar Cheese", "Greek Yogurt", "Heavy Cream",
  "Chicken Breast", "Chicken Thighs", "Ground Beef", "Bacon", "Salmon", "Shrimp",
  "Apples", "Bananas", "Onions", "Garlic", "Tomatoes", "Bell Peppers", "Spinach",
  "Broccoli", "Carrots", "Potatoes", "Lemons", "Avocado", "Lettuce", "Mushrooms",
  "Bread", "Bagels", "Tortillas", "Rice", "Pasta", "Flour", "Sugar", "Olive Oil",
  "Black Beans", "Chickpeas", "Canned Tomatoes", "Chicken Broth", "Oats", "Cereal",
  "Ketchup", "Mustard", "Mayonnaise", "Soy Sauce", "Salsa", "Peanut Butter",
  "Coffee", "Orange Juice", "Sparkling Water", "Frozen Peas", "Ice Cream",
  "Tortilla Chips", "Crackers", "Almonds", "Salt", "Black Pepper", "Paprika",
];

export function suggestItems(query: string, limit = 6): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const starts = COMMON_ITEMS.filter((i) => i.toLowerCase().startsWith(q));
  const contains = COMMON_ITEMS.filter(
    (i) => !i.toLowerCase().startsWith(q) && i.toLowerCase().includes(q),
  );
  return [...starts, ...contains].slice(0, limit);
}
