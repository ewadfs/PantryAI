"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { currentWeekStart } from "@/lib/week";
import {
  buildShoppingList,
  generateRecipes,
  getWeek,
  markCooked,
  rateRecipe,
  removeFromWeek,
  saveToWeek,
} from "@/lib/recipeApi";
import type { Recipe, WeekResponse } from "@/lib/recipeTypes";
import RecipeCard from "@/components/recipes/RecipeCard";
import RecipeSheet from "@/components/recipes/RecipeSheet";
import ThisWeek from "@/components/recipes/ThisWeek";
import Confetti from "@/components/recipes/Confetti";

const STEPS = [
  "Reading your pantry…",
  "Checking this week's deals…",
  "Writing your recipes…",
];

export default function RecipesPage() {
  const router = useRouter();
  const weekStart = useMemo(() => currentWeekStart(), []);

  const [week, setWeek] = useState<WeekResponse | null>(null);
  const [recipes, setRecipes] = useState<Recipe[] | null>(null);
  const [generating, setGenerating] = useState(false);
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Recipe | null>(null);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [cookingId, setCookingId] = useState<number | null>(null);
  const [buildingList, setBuildingList] = useState(false);
  const [confetti, setConfetti] = useState(false);
  const stepTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const savedIds = useMemo(
    () => new Set((week?.recipes ?? []).map((w) => w.recipe.id)),
    [week],
  );

  const loadWeek = useCallback(async () => {
    try {
      setWeek(await getWeek(weekStart));
    } catch {
      /* week is optional context; ignore load errors */
    }
  }, [weekStart]);

  const generate = useCallback(async () => {
    setError(null);
    setRecipes(null);
    setGenerating(true);
    setStep(0);
    stepTimer.current = setInterval(
      () => setStep((s) => Math.min(s + 1, STEPS.length - 1)),
      5000,
    );
    try {
      const res = await generateRecipes();
      setRecipes(res.recipes);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate recipes.");
    } finally {
      if (stepTimer.current) clearInterval(stepTimer.current);
      setGenerating(false);
    }
  }, []);

  // Initial load + optional auto-generate from ?generate=1.
  useEffect(() => {
    loadWeek();
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      if (params.get("generate") === "1") {
        window.history.replaceState({}, "", "/recipes");
        generate();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyRating(id: number, rating: number) {
    setRecipes((prev) =>
      prev ? prev.map((r) => (r.id === id ? { ...r, rating } : r)) : prev,
    );
    setWeek((prev) =>
      prev
        ? {
            ...prev,
            recipes: prev.recipes.map((w) =>
              w.recipe.id === id ? { ...w, recipe: { ...w.recipe, rating } } : w,
            ),
          }
        : prev,
    );
    setSelected((prev) => (prev && prev.id === id ? { ...prev, rating } : prev));
  }

  async function onRate(id: number, rating: 1 | -1) {
    const prevRating =
      recipes?.find((r) => r.id === id)?.rating ??
      week?.recipes.find((w) => w.recipe.id === id)?.recipe.rating ??
      null;
    applyRating(id, rating); // optimistic
    try {
      await rateRecipe(id, rating);
    } catch {
      applyRating(id, prevRating ?? 0); // revert
    }
  }

  async function onSave(id: number) {
    if (savedIds.has(id)) return;
    setSavingId(id);
    try {
      await saveToWeek(id, weekStart);
      await loadWeek();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save to this week.");
    } finally {
      setSavingId(null);
    }
  }

  async function onCooked(id: number) {
    setCookingId(id);
    try {
      await markCooked(weekStart, id);
      setWeek((prev) =>
        prev
          ? {
              ...prev,
              recipes: prev.recipes.map((w) =>
                w.recipe.id === id ? { ...w, is_cooked: true } : w,
              ),
            }
          : prev,
      );
      setConfetti(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not mark as cooked.");
    } finally {
      setCookingId(null);
    }
  }

  async function onRemove(id: number) {
    const snapshot = week;
    setWeek((prev) =>
      prev ? { ...prev, recipes: prev.recipes.filter((w) => w.recipe.id !== id) } : prev,
    );
    try {
      await removeFromWeek(weekStart, id);
    } catch {
      setWeek(snapshot); // revert
    }
  }

  async function onBuildList() {
    setBuildingList(true);
    try {
      await buildShoppingList(weekStart);
      router.push("/list");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not build the list.");
      setBuildingList(false);
    }
  }

  return (
    <div className="px-5 pt-8">
      {confetti && <Confetti onDone={() => setConfetti(false)} />}

      <h1 className="mb-6 text-2xl font-bold text-ink">Recipes</h1>

      <ThisWeek
        week={week}
        cookingId={cookingId}
        buildingList={buildingList}
        onCooked={onCooked}
        onRemove={onRemove}
        onOpen={setSelected}
        onBuildList={onBuildList}
      />

      {/* Generate area */}
      {!recipes && !generating && (
        <div className="rounded-2xl border border-hairline bg-surface p-6 text-center">
          <p className="text-base font-semibold text-ink">Tonight&apos;s dinner, sorted</p>
          <p className="mt-1 text-sm text-ink-soft">
            Three options built from your pantry and this week&apos;s deals.
          </p>
          <button
            onClick={generate}
            className="mt-5 flex h-14 w-full items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white shadow-lg shadow-brand/25 active:scale-[.99]"
          >
            🍳 Generate 3 recipes
          </button>
        </div>
      )}

      {generating && (
        <div>
          <p className="mb-4 text-center text-sm font-medium text-ink-soft">{STEPS[step]}</p>
          <div className="flex flex-col gap-4">
            {[0, 1, 2].map((i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        </div>
      )}

      {error && (
        <p className="mt-4 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">
          {error}
        </p>
      )}

      {recipes && !generating && (
        <div>
          <div className="flex flex-col gap-4">
            {recipes.map((r) => (
              <RecipeCard
                key={r.id}
                recipe={r}
                saved={savedIds.has(r.id)}
                savstate={savingId === r.id ? "saving" : "idle"}
                onSave={() => onSave(r.id)}
                onRate={(rating) => onRate(r.id, rating)}
                onExpand={() => setSelected(r)}
              />
            ))}
          </div>
          <button
            onClick={generate}
            className="mt-4 flex h-12 w-full items-center justify-center rounded-2xl border border-hairline bg-surface text-sm font-semibold text-ink active:scale-[.99]"
          >
            Show me different options
          </button>
        </div>
      )}

      {selected && (
        <RecipeSheet
          recipe={selected}
          saved={savedIds.has(selected.id)}
          onSave={() => onSave(selected.id)}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

function SkeletonCard() {
  return (
    <div className="rounded-2xl border border-hairline bg-surface p-5">
      <div className="skeleton h-5 w-16 rounded-full" />
      <div className="skeleton mt-3 h-6 w-3/4 rounded" />
      <div className="skeleton mt-2 h-4 w-1/2 rounded" />
      <div className="skeleton mt-4 h-4 w-2/3 rounded" />
      <div className="skeleton mt-2 h-4 w-1/3 rounded" />
      <div className="skeleton mt-4 h-11 w-full rounded-xl" />
    </div>
  );
}
