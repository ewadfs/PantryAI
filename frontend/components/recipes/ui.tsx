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

export function metaLine(r: Recipe): string {
  const parts: string[] = [];
  const t = totalMinutes(r);
  if (t != null) parts.push(`${t} min`);
  if (r.servings != null) parts.push(`serves ${r.servings}`);
  const n = r.nutrition_per_serving;
  if (n?.calories != null) parts.push(`${Math.round(n.calories)} cal`);
  if (n?.protein_g != null) parts.push(`${Math.round(n.protein_g)}g protein`);
  return parts.join(" · ");
}

/** Honest cost line — never invents a total. */
export function CostLine({ recipe }: { recipe: Recipe }) {
  const known = Number(recipe.cost.known_buy_cost ?? 0);
  const unknown = recipe.cost.unknown_priced_items;
  const saleCount = recipe.ingredients.filter((i) => i.on_sale).length;

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

export function PantryLine({ recipe }: { recipe: Recipe }) {
  const have = recipe.cost.pantry_items_used;
  const total = recipe.ingredients.length;
  return (
    <p className="text-sm text-ink-soft">
      🏠 Have {have} of {total} ingredient{total === 1 ? "" : "s"}
    </p>
  );
}
