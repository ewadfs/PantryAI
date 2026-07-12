"use client";

import { useEffect, useRef, useState } from "react";
import { getRecipe } from "@/lib/recipeApi";
import { comparePricesForRecipe, type PriceCompareResponse } from "@/lib/pricesApi";
import type { Recipe } from "@/lib/recipeTypes";
import {
  AllPantryBadge,
  DifficultyPill,
  MarketPickBadge,
  QualityChips,
  marketAnchorLine,
  metaLine,
  nutritionLine,
  nutritionTag,
} from "./ui";

type LooseIng = {
  generic_name?: string | null;
  name?: string | null;
  brand?: string | null;
  quantity?: string | number | null;
  unit?: string | null;
  in_pantry: boolean | "partial";
  pantry_quantity?: string | null;
  shortfall_quantity?: string | null;
  on_sale: boolean;
  sale_store?: string | null;
  sale_price?: string | number | null;
};

function IngredientRow({ ing }: { ing: LooseIng }) {
  const label = ing.generic_name || ing.name || "";
  const qty = [ing.quantity, ing.unit].filter(Boolean).join(" ");
  let icon = "🛒";
  let tail: React.ReactNode = <span className="text-ink-faint">—</span>;
  if (ing.in_pantry === "partial") {
    icon = "🟡";
    tail = (
      <span className="text-warn">
        have {ing.pantry_quantity ?? "some"}
        {ing.shortfall_quantity ? ` — buy ${ing.shortfall_quantity} more` : ""}
      </span>
    );
  } else if (ing.in_pantry) {
    icon = "🏠";
    tail = <span className="text-brand-dark">have</span>;
  } else if (ing.on_sale) {
    icon = "🏷️";
    tail = (
      <span className="text-brand-dark">
        {ing.sale_store ? `${ing.sale_store} ` : ""}
        {ing.sale_price != null ? `$${Number(ing.sale_price).toFixed(2)}` : ""}
      </span>
    );
  }
  return (
    <li className="flex items-center gap-3 border-t border-hairline py-2.5 first:border-t-0">
      <span className="text-lg" aria-hidden>
        {icon}
      </span>
      <span className="min-w-0 flex-1 text-base text-ink">
        {qty && <span className="font-medium">{qty} </span>}
        {label}
        {ing.brand && <span className="ml-1 text-xs text-ink-faint">{ing.brand}</span>}
      </span>
      <span className="shrink-0 text-sm">{tail}</span>
    </li>
  );
}

function money(v: string | number) {
  return `$${Number(v).toFixed(2)}`;
}

function BuyAtRow({ prices }: { prices: PriceCompareResponse }) {
  // Anchor store (default) first, then the rest as returned by the backend.
  const stores = [...prices.stores].sort(
    (a, b) => Number(b.is_default) - Number(a.is_default),
  );
  return (
    <div className="mt-4 rounded-2xl border border-hairline bg-canvas p-3">
      <div className="flex items-baseline justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">
          Buy at
        </p>
        <p className="text-[11px] text-ink-faint">
          known deals · {prices.needed_count} to buy
        </p>
      </div>
      <div className="mt-2 flex gap-4 overflow-x-auto whitespace-nowrap pb-1">
        {stores.map((s) => {
          const name = s.chain_name ?? s.store_name ?? "Store";
          const hasPrices = s.priced_count > 0;
          return (
            <div key={s.store_id} className="shrink-0">
              <p className={`text-sm font-medium ${s.is_default ? "text-brand-dark" : "text-ink"}`}>
                {name}
                {s.is_default && <span className="ml-1 text-[11px]">• this week</span>}
              </p>
              <p className="text-sm">
                {hasPrices ? (
                  <span className="font-semibold text-ink">{money(s.known_cost_sum)}</span>
                ) : (
                  <span className="text-ink-faint">—</span>
                )}
                {s.unpriced_count > 0 && (
                  <span className="ml-1 text-[11px] text-ink-faint">
                    +{s.unpriced_count} unpriced
                  </span>
                )}
              </p>
            </div>
          );
        })}
      </div>
      <p className="mt-1 text-[11px] text-ink-faint">
        Prices shown come from current flyers. “—” means no deal listed there, not
        that it isn’t sold.
      </p>
    </div>
  );
}

export default function RecipeSheet({
  recipe,
  saved,
  onSave,
  onClose,
  onUpdate,
}: {
  recipe: Recipe;
  saved: boolean;
  onSave: () => void;
  onClose: () => void;
  // Push freshly-detailed data back up so the card behind the sheet re-renders
  // (B4: no stale concept nutrition/cost once details land).
  onUpdate?: (r: Recipe) => void;
}) {
  const [data, setData] = useState<Recipe>(recipe);
  const [prices, setPrices] = useState<PriceCompareResponse | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => setData(recipe), [recipe]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // Poll for full details while this is still a concept.
  useEffect(() => {
    if (data.status !== "concept") return;
    timer.current = setInterval(async () => {
      try {
        const fresh = await getRecipe(data.id);
        if (fresh.status === "ready") {
          setData(fresh);
          onUpdate?.(fresh);
          if (timer.current) clearInterval(timer.current);
        }
      } catch {
        /* keep polling */
      }
    }, 2000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [data.status, data.id]);

  // Fetch cross-store prices once the recipe is ready and needs ≥1 purchase.
  const needsBuying =
    data.status === "ready" &&
    data.ingredients.some((ing) => (ing as LooseIng).in_pantry !== true);
  useEffect(() => {
    if (!needsBuying) return;
    let cancelled = false;
    comparePricesForRecipe(data.id)
      .then((res) => {
        if (!cancelled) setPrices(res);
      })
      .catch(() => {
        /* pricing is best-effort */
      });
    return () => {
      cancelled = true;
    };
  }, [needsBuying, data.id]);

  const n = data.nutrition_per_serving;
  const isConcept = data.status === "concept" || data.ingredients.length === 0;

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end">
      <button aria-label="Close" onClick={onClose} className="absolute inset-0 bg-ink/40" />
      <div className="relative mx-auto max-h-[92vh] w-full max-w-md overflow-y-auto rounded-t-3xl bg-surface pb-28">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-hairline bg-surface px-5 py-3">
          <span className="text-sm font-semibold text-ink-soft">Recipe</span>
          <button onClick={onClose} className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft" aria-label="Close">
            <span className="text-lg">✕</span>
          </button>
        </div>

        <div className="px-5 pt-4">
          <div className="flex flex-wrap items-center gap-2">
            <DifficultyPill difficulty={data.difficulty} />
            {data.is_market_pick && <MarketPickBadge />}
            <AllPantryBadge recipe={data} />
            <QualityChips recipe={data} />
          </div>
          <h2 className="mt-2 text-2xl font-bold text-ink">{data.title}</h2>
          {marketAnchorLine(data) && (
            <p className="mt-1 text-sm font-medium text-warn">{marketAnchorLine(data)}</p>
          )}
          <p className="mt-1 text-sm text-ink-soft">{metaLine(data)}</p>
          {nutritionLine(data) && (
            <p className="text-sm text-ink-soft">{nutritionLine(data)}</p>
          )}
          {data.description && <p className="mt-3 text-base text-ink">{data.description}</p>}
          {data.why_this_recipe && (
            <p className="mt-2 text-sm italic text-ink-soft">“{data.why_this_recipe}”</p>
          )}

          <h3 className="mt-6 text-sm font-semibold uppercase tracking-wide text-ink-faint">
            Ingredients
          </h3>
          <ul className="mt-2">
            {(isConcept ? data.key_ingredients : data.ingredients).map((ing, i) => (
              <IngredientRow key={i} ing={ing as LooseIng} />
            ))}
          </ul>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-faint">
            <span>🏠 have</span>
            <span>🟡 have some</span>
            <span>🏷️ on sale</span>
            <span>🛒 need</span>
          </div>

          {prices && prices.needed_count > 0 && prices.stores.length > 0 && (
            <BuyAtRow prices={prices} />
          )}

          {isConcept ? (
            <div className="mt-6">
              <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-faint">
                Instructions
              </h3>
              <p className="mb-3 flex items-center gap-2 text-sm text-ink-soft">
                <span className="inline-block h-3 w-3 animate-ping rounded-full bg-brand-soft" />
                Writing the full recipe…
              </p>
              <div className="space-y-2">
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className="skeleton h-5 w-full rounded" />
                ))}
              </div>
            </div>
          ) : (
            <>
              <h3 className="mt-6 text-sm font-semibold uppercase tracking-wide text-ink-faint">
                Instructions
              </h3>
              <ol className="mt-3 space-y-4">
                {data.instructions.map((step, i) => (
                  <li key={i} className="flex gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-soft text-base font-bold text-brand-dark">
                      {i + 1}
                    </span>
                    <p className="pt-0.5 text-lg leading-relaxed text-ink">{step}</p>
                  </li>
                ))}
              </ol>
            </>
          )}

          {n && (
            <>
              <h3 className="mt-6 text-sm font-semibold uppercase tracking-wide text-ink-faint">
                Nutrition (per serving, {nutritionTag(data)})
              </h3>
              <div className="mt-2 grid grid-cols-5 gap-2 text-center">
                {[
                  ["Cal", n.calories],
                  ["Protein", n.protein_g],
                  ["Carbs", n.carbs_g],
                  ["Fat", n.fat_g],
                  ["Fiber", n.fiber_g],
                ].map(([label, val]) => (
                  <div key={label as string} className="rounded-xl bg-canvas p-2">
                    <p className="text-base font-bold text-ink">
                      {val == null ? "—" : Math.round(Number(val))}
                    </p>
                    <p className="text-[11px] text-ink-soft">{label}</p>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        <div className="fixed inset-x-0 bottom-0 mx-auto max-w-md border-t border-hairline bg-surface px-5 py-3">
          <button
            onClick={onSave}
            disabled={saved}
            className={`flex w-full items-center justify-center rounded-2xl py-4 text-base font-semibold ${
              saved ? "bg-brand-soft text-brand-dark" : "bg-brand text-white"
            }`}
          >
            {saved ? "✓ Saved to this week" : "Save to this week"}
          </button>
        </div>
      </div>
    </div>
  );
}
