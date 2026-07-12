import type { Recipe } from "@/lib/recipeTypes";

export function DifficultyPill({ difficulty }: { difficulty: string | null }) {
  const d = (difficulty ?? "").toLowerCase();
  const style =
    d === "easy"
      ? "bg-brand-soft text-brand-dark"
      : d === "medium"
        ? "bg-warn-soft text-warn"
        : d === "hard"
          ? "bg-red-100 text-red-600"
          : "bg-hairline text-ink-soft";
  return (
    <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold capitalize ${style}`}>
      {difficulty ?? "recipe"}
    </span>
  );
}

export function totalMinutes(r: Recipe): number | null {
  if (r.total_time_min != null) return r.total_time_min;
  if (r.prep_time_min != null || r.cook_time_min != null)
    return (r.prep_time_min ?? 0) + (r.cook_time_min ?? 0);
  return null;
}

/** Ingredients to reason about — full when 'ready', else the concept's key list. */
export function effectiveIngredients(
  r: Recipe,
): { in_pantry: boolean | "partial"; on_sale: boolean }[] {
  return r.ingredients.length ? r.ingredients : r.key_ingredients;
}

export function metaLine(r: Recipe): string {
  const parts: string[] = [];
  const t = totalMinutes(r);
  if (t != null) parts.push(`${t} min`);
  if (r.servings != null) parts.push(`serves ${r.servings}`);
  return parts.join(" · ");
}

/** Label for a nutrition figure: deterministic USDA compute vs model estimate. */
export function nutritionTag(r: Recipe): "calculated" | "est." {
  return r.nutrition_per_serving?.source === "calculated" ? "calculated" : "est.";
}

export function nutritionLine(r: Recipe): string | null {
  const n = r.nutrition_per_serving;
  if (!n || (n.calories == null && n.protein_g == null)) return null;
  const bits: string[] = [];
  if (n.calories != null) bits.push(`${Math.round(n.calories)} cal`);
  if (n.protein_g != null) bits.push(`${Math.round(n.protein_g)}g protein`);
  const calc = n.source === "calculated";
  // "≈" only for estimates; a computed figure is exact enough to stand plain.
  return `${calc ? "" : "≈ "}${bits.join(" · ")} · ${calc ? "calculated" : "est."}`;
}

/** Honest cost line — never invents a total. */
export function CostLine({ recipe }: { recipe: Recipe }) {
  const known = Number(recipe.cost.known_buy_cost ?? 0);
  const unknown = recipe.cost.unknown_priced_items;
  const saleCount = effectiveIngredients(recipe).filter((i) => i.on_sale).length;

  if (known <= 0 && unknown === 0) {
    return (
      <p className="text-sm font-semibold text-brand-dark">
        ✅ Everything&apos;s in your pantry
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      {known > 0 && (
        <span className="font-semibold text-ink">Buy: ${known.toFixed(2)}</span>
      )}
      {saleCount > 0 && (
        <span className="rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-semibold text-brand-dark">
          🏷️ {saleCount} on sale
        </span>
      )}
      {unknown > 0 && (
        <span className="text-ink-soft">
          + {unknown} item{unknown === 1 ? "" : "s"}, price unknown
        </span>
      )}
    </div>
  );
}

/** "Built around: pork shoulder, $1.99/lb this week" for a market pick;
 * a cross-store anchor names its store ("… — at Stop & Shop"). */
export function marketAnchorLine(r: Recipe): string | null {
  if (!r.is_market_pick || !r.market_anchor) return null;
  const a = r.market_anchor;
  const price =
    a.sale_price != null
      ? `$${Number(a.sale_price).toFixed(2)}${a.price_unit ? `/${a.price_unit}` : ""}`
      : null;
  const at = a.cross_store && a.store ? ` — at ${a.store}` : "";
  return `Built around: ${a.name}${price ? `, ${price} this week` : ""}${at}`;
}

/** Amber honesty chips: a sub-floor or heavy recipe never renders its numbers
 * unannotated — on the card AND the detail sheet. */
export function QualityChips({ recipe }: { recipe: Recipe }) {
  const f = recipe.quality_flags;
  if (!f || (!f.protein_below_floor && !f.heavy)) return null;
  const chip =
    "inline-flex items-center rounded-full bg-warn-soft px-2 py-1 text-[11px] font-semibold text-warn";
  return (
    <>
      {f.protein_below_floor && (
        <span className={chip}>
          ⚠ {Math.round(f.protein_below_floor.protein_g)}g protein — below your{" "}
          {Math.round(f.protein_below_floor.floor_g)}g target
        </span>
      )}
      {f.heavy && (
        <span className={chip}>⚠ heavy: {Math.round(f.heavy.calories)} cal</span>
      )}
    </>
  );
}

export function MarketPickBadge() {
  return (
    <span className="inline-flex items-center rounded-full bg-warn-soft px-2 py-1 text-[11px] font-semibold text-warn">
      🏷️ Market pick
    </span>
  );
}

/** Subtle accent for the all-pantry dish — it leads its tier in the feed and
 * should read as the headline it is, not the unbadged leftover. */
export function AllPantryBadge({ recipe }: { recipe: Recipe }) {
  const known = Number(recipe.cost.known_buy_cost ?? 0);
  if (known > 0 || recipe.cost.unknown_priced_items > 0) return null;
  if (effectiveIngredients(recipe).length === 0) return null;
  return (
    <span className="inline-flex items-center rounded-full bg-brand-soft px-2 py-1 text-[11px] font-semibold text-brand-dark">
      🏠 All pantry · $0
    </span>
  );
}

export function PantryLine({ recipe }: { recipe: Recipe }) {
  const ings = effectiveIngredients(recipe);
  const total = ings.length;
  // Partial counts as needing a purchase — only full holdings count as "have".
  const have = ings.filter((i) => i.in_pantry === true).length;
  const partial = ings.filter((i) => i.in_pantry === "partial").length;
  return (
    <p className="text-sm text-ink-soft">
      🏠 Have {have} of {total} ingredient{total === 1 ? "" : "s"}
      {partial > 0 && (
        <span className="text-warn"> ({partial} partial)</span>
      )}
    </p>
  );
}
