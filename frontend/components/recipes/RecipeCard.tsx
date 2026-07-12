"use client";

import type { Recipe } from "@/lib/recipeTypes";
import {
  CostLine,
  DifficultyPill,
  MarketPickBadge,
  PantryLine,
  marketAnchorLine,
  metaLine,
  nutritionLine,
} from "./ui";

export default function RecipeCard({
  recipe,
  saved,
  savstate,
  onSave,
  onRate,
  onExpand,
}: {
  recipe: Recipe;
  saved: boolean;
  savstate?: "idle" | "saving";
  onSave: () => void;
  onRate: (rating: 1 | -1) => void;
  onExpand: () => void;
}) {
  const rating = recipe.rating;
  return (
    <article className="rounded-2xl border border-hairline bg-surface p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <DifficultyPill difficulty={recipe.difficulty} />
            {recipe.is_market_pick && <MarketPickBadge />}
          </div>
          <h3 className="mt-2 text-lg font-bold leading-snug text-ink">{recipe.title}</h3>
        </div>
      </div>

      {marketAnchorLine(recipe) && (
        <p className="mt-1 text-sm font-medium text-warn">{marketAnchorLine(recipe)}</p>
      )}
      <p className="mt-1 text-sm text-ink-soft">{metaLine(recipe)}</p>
      {nutritionLine(recipe) && (
        <p className="mt-0.5 text-sm text-ink-soft">{nutritionLine(recipe)}</p>
      )}

      <div className="mt-3 space-y-1.5">
        <PantryLine recipe={recipe} />
        <CostLine recipe={recipe} />
      </div>

      {recipe.why_this_recipe && (
        <p className="mt-3 text-sm italic text-ink-soft">“{recipe.why_this_recipe}”</p>
      )}

      <div className="mt-4 flex items-center gap-2">
        <button
          onClick={onSave}
          disabled={saved || savstate === "saving"}
          className={`flex h-11 flex-1 items-center justify-center rounded-xl text-sm font-semibold transition active:scale-[.99] ${
            saved
              ? "bg-brand-soft text-brand-dark"
              : "bg-brand text-white disabled:opacity-60"
          }`}
        >
          {saved ? "✓ In this week" : savstate === "saving" ? "Saving…" : "Save to this week"}
        </button>

        <button
          aria-label="Thumbs up"
          onClick={() => onRate(1)}
          className={`flex h-11 w-11 items-center justify-center rounded-xl border text-lg transition active:scale-95 ${
            rating === 1 ? "border-brand bg-brand-soft" : "border-hairline bg-surface"
          }`}
        >
          👍
        </button>
        <button
          aria-label="Thumbs down"
          onClick={() => onRate(-1)}
          className={`flex h-11 w-11 items-center justify-center rounded-xl border text-lg transition active:scale-95 ${
            rating === -1 ? "border-warn bg-warn-soft" : "border-hairline bg-surface"
          }`}
        >
          👎
        </button>
      </div>

      <button
        onClick={onExpand}
        className="mt-3 flex w-full items-center justify-center gap-1 text-sm font-medium text-brand-dark"
      >
        View recipe
        <span aria-hidden>›</span>
      </button>
    </article>
  );
}
