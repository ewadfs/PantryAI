"use client";

import { useState } from "react";
import { planWeek, type WeekPlanEstimate } from "@/lib/recipeApi";
import type { Difficulty } from "@/lib/recipeApi";

/**
 * "Plan my week" (P42 A): the This Week empty-state hero. One choice —
 * dinner count — everything else rides the current setup (store, tiers,
 * pantry mode). The coordinated set saves straight to This Week; on success
 * the parent refreshes the week and we surface the build-list prompt.
 */
export default function WeekPlanner({
  storeName,
  difficulties,
  pantryMode,
  onPlanned,
  onError,
}: {
  storeName: string | null;
  difficulties: Difficulty[];
  pantryMode: boolean;
  onPlanned: (estimate: WeekPlanEstimate) => Promise<void> | void;
  onError: (message: string) => void;
}) {
  const [dinners, setDinners] = useState<3 | 4 | 5>(4);
  const [planning, setPlanning] = useState(false);

  async function onPlan() {
    setPlanning(true);
    try {
      const res = await planWeek(dinners, difficulties, pantryMode);
      await onPlanned(res.estimate);
    } catch (e) {
      onError(e instanceof Error ? e.message : "Couldn't plan the week.");
    } finally {
      setPlanning(false);
    }
  }

  if (planning) {
    return (
      <div className="rounded-2xl border border-hairline bg-surface p-8 text-center">
        <div className="text-4xl" aria-hidden>🗓️</div>
        <p className="mt-3 text-base font-semibold text-ink">
          Planning your week…
        </p>
        <p className="mt-1 text-sm text-ink-soft">
          Coordinating {dinners} dinners{storeName ? ` around ${storeName}'s deals` : ""} —
          shared ingredients, one smart shop. ~45 seconds.
        </p>
        <div className="mt-5 flex flex-col gap-3">
          {Array.from({ length: dinners }, (_, i) => (
            <div key={i} className="skeleton h-12 rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-hairline bg-surface p-6 text-center">
      <div className="text-4xl" aria-hidden>🗓️</div>
      <h2 className="mt-3 text-lg font-bold text-ink">Plan my week</h2>
      <p className="mt-1 text-sm text-ink-soft">
        One coordinated set of dinners{storeName ? ` from ${storeName}'s flyer` : ""}:
        shared ingredients, the best deal stretched across meals, easier
        nights first.
      </p>
      <div className="mt-5 flex items-center justify-center gap-2">
        {([3, 4, 5] as const).map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => setDinners(n)}
            className={`h-11 w-16 rounded-2xl border text-base font-bold transition active:scale-95 ${
              dinners === n
                ? "border-brand bg-brand text-white"
                : "border-hairline bg-surface text-ink-soft"
            }`}
          >
            {n}
          </button>
        ))}
        <span className="ml-1 text-sm text-ink-soft">dinners</span>
      </div>
      <button
        type="button"
        onClick={onPlan}
        className="mt-5 flex h-12 w-full items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white shadow-lg shadow-brand/25 active:scale-[.99]"
      >
        🗓️ Plan {dinners} dinners
      </button>
    </div>
  );
}
