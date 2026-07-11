"use client";

import type { Recipe, WeekResponse } from "@/lib/recipeTypes";
import { DifficultyPill } from "./ui";

export default function ThisWeek({
  week,
  cookingId,
  buildingList,
  onCooked,
  onRemove,
  onOpen,
  onBuildList,
}: {
  week: WeekResponse | null;
  cookingId: number | null;
  buildingList: boolean;
  onCooked: (id: number) => void;
  onRemove: (id: number) => void;
  onOpen: (recipe: Recipe) => void;
  onBuildList: () => void;
}) {
  const rows = week?.recipes ?? [];
  if (rows.length === 0) return null;

  return (
    <section className="mb-8">
      <h2 className="mb-3 text-lg font-bold text-ink">This week</h2>

      <div className="flex flex-col gap-2">
        {rows.map((wr) => (
          <div
            key={wr.recipe.id}
            className="rounded-2xl border border-hairline bg-surface p-4"
          >
            <div className="flex items-start gap-3">
              <button onClick={() => onOpen(wr.recipe)} className="min-w-0 flex-1 text-left">
                <DifficultyPill difficulty={wr.recipe.difficulty} />
                <p className="mt-1.5 truncate text-base font-semibold text-ink">
                  {wr.recipe.title}
                </p>
              </button>
              <button
                aria-label="Remove from week"
                onClick={() => onRemove(wr.recipe.id)}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-ink-faint active:scale-90"
              >
                <span className="text-lg">✕</span>
              </button>
            </div>

            {wr.is_cooked ? (
              <p className="mt-3 flex items-center gap-1.5 text-sm font-semibold text-brand-dark">
                ✅ Cooked · pantry updated
              </p>
            ) : (
              <button
                onClick={() => onCooked(wr.recipe.id)}
                disabled={cookingId === wr.recipe.id}
                className="mt-3 flex h-10 w-full items-center justify-center rounded-xl border border-brand/40 bg-brand-soft text-sm font-semibold text-brand-dark transition active:scale-[.99] disabled:opacity-60"
              >
                {cookingId === wr.recipe.id ? "Updating…" : "Cooked it ✅"}
              </button>
            )}
          </div>
        ))}
      </div>

      <button
        onClick={onBuildList}
        disabled={buildingList}
        className="mt-4 flex h-14 w-full items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white shadow-lg shadow-brand/25 transition active:scale-[.99] disabled:opacity-60"
      >
        {buildingList ? "Building…" : "🛒 Build shopping list"}
      </button>
    </section>
  );
}
