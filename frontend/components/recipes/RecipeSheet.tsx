"use client";

import { useEffect, useRef, useState } from "react";
import { getRecipe } from "@/lib/recipeApi";
import type { Recipe } from "@/lib/recipeTypes";
import { DifficultyPill, metaLine, nutritionLine } from "./ui";

type LooseIng = {
  generic_name?: string | null;
  name?: string | null;
  brand?: string | null;
  quantity?: string | number | null;
  unit?: string | null;
  in_pantry: boolean;
  on_sale: boolean;
  sale_store?: string | null;
  sale_price?: string | number | null;
};

function IngredientRow({ ing }: { ing: LooseIng }) {
  const label = ing.generic_name || ing.name || "";
  const qty = [ing.quantity, ing.unit].filter(Boolean).join(" ");
  let icon = "🛒";
  let tail: React.ReactNode = <span className="text-ink-faint">—</span>;
  if (ing.in_pantry) {
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

export default function RecipeSheet({
  recipe,
  saved,
  onSave,
  onClose,
}: {
  recipe: Recipe;
  saved: boolean;
  onSave: () => void;
  onClose: () => void;
}) {
  const [data, setData] = useState<Recipe>(recipe);
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
          <DifficultyPill difficulty={data.difficulty} />
          <h2 className="mt-2 text-2xl font-bold text-ink">{data.title}</h2>
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
            <span>🏷️ on sale</span>
            <span>🛒 need</span>
          </div>

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
                Nutrition (per serving, est.)
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
