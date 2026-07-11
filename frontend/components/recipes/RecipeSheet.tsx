"use client";

import { useEffect } from "react";
import type { Recipe, RecipeIngredient } from "@/lib/recipeTypes";
import { DifficultyPill, metaLine } from "./ui";

function IngredientRow({ ing }: { ing: RecipeIngredient }) {
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
        {ing.name}
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
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  const n = recipe.nutrition_per_serving;

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end">
      <button
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-ink/40"
      />
      <div className="relative mx-auto max-h-[92vh] w-full max-w-md overflow-y-auto rounded-t-3xl bg-surface pb-28">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-hairline bg-surface px-5 py-3">
          <span className="text-sm font-semibold text-ink-soft">Recipe</span>
          <button
            onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft"
            aria-label="Close"
          >
            <span className="text-lg">✕</span>
          </button>
        </div>

        <div className="px-5 pt-4">
          <DifficultyPill difficulty={recipe.difficulty} />
          <h2 className="mt-2 text-2xl font-bold text-ink">{recipe.title}</h2>
          <p className="mt-1 text-sm text-ink-soft">{metaLine(recipe)}</p>
          {recipe.description && (
            <p className="mt-3 text-base text-ink">{recipe.description}</p>
          )}
          {recipe.why_this_recipe && (
            <p className="mt-2 text-sm italic text-ink-soft">“{recipe.why_this_recipe}”</p>
          )}

          <h3 className="mt-6 text-sm font-semibold uppercase tracking-wide text-ink-faint">
            Ingredients
          </h3>
          <ul className="mt-2">
            {recipe.ingredients.map((ing, i) => (
              <IngredientRow key={i} ing={ing} />
            ))}
          </ul>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-faint">
            <span>🏠 have</span>
            <span>🏷️ on sale</span>
            <span>🛒 need</span>
          </div>

          <h3 className="mt-6 text-sm font-semibold uppercase tracking-wide text-ink-faint">
            Instructions
          </h3>
          <ol className="mt-3 space-y-4">
            {recipe.instructions.map((step, i) => (
              <li key={i} className="flex gap-3">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-soft text-base font-bold text-brand-dark">
                  {i + 1}
                </span>
                <p className="pt-0.5 text-lg leading-relaxed text-ink">{step}</p>
              </li>
            ))}
          </ol>

          {n && (
            <>
              <h3 className="mt-6 text-sm font-semibold uppercase tracking-wide text-ink-faint">
                Nutrition (per serving)
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
