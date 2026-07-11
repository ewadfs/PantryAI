"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { currentWeekStart } from "@/lib/week";
import {
  buildShoppingList,
  generateRecipes,
  getLatestRecipes,
  getWeek,
  markCooked,
  rateRecipe,
  removeFromWeek,
  saveToWeek,
} from "@/lib/recipeApi";
import { getMyStores, setDefaultStore } from "@/lib/storesApi";
import type { UserStore } from "@/lib/listTypes";
import type { Recipe, WeekResponse } from "@/lib/recipeTypes";
import RecipeCard from "@/components/recipes/RecipeCard";
import RecipeSheet from "@/components/recipes/RecipeSheet";
import StoreSheet from "@/components/recipes/StoreSheet";
import ThisWeek from "@/components/recipes/ThisWeek";
import Confetti from "@/components/recipes/Confetti";

const STEPS = [
  "Reading your pantry…",
  "Checking this week's deals…",
  "Writing your recipes…",
];

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const h = Math.floor(mins / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

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
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [warming, setWarming] = useState(true);
  const [stores, setStores] = useState<UserStore[]>([]);
  const [storeSheet, setStoreSheet] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [batchStore, setBatchStore] = useState<string | null>(null);
  const stepTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const currentStore = stores.find((s) => s.is_default) ?? stores[0] ?? null;
  const currentStoreName = currentStore?.store.store_name ?? null;
  const stale =
    !!batchStore && !!currentStoreName && batchStore !== currentStoreName;

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
      setGeneratedAt(res.recipes[0]?.generated_at ?? new Date().toISOString());
      // Fresh batch is anchored to the current default store.
      setStores((prev) => {
        const name = (prev.find((s) => s.is_default) ?? prev[0])?.store.store_name;
        setBatchStore(name ?? null);
        return prev;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate recipes.");
    } finally {
      if (stepTimer.current) clearInterval(stepTimer.current);
      setGenerating(false);
    }
  }, []);

  // Warm start: render the most recent batch immediately (any status).
  const loadLatest = useCallback(async () => {
    try {
      const res = await getLatestRecipes();
      if (res.recipes.length) {
        setRecipes(res.recipes);
        setGeneratedAt(res.generated_at);
        setBatchStore(res.store_name);
      }
    } catch {
      /* ignore — user can generate */
    } finally {
      setWarming(false);
    }
  }, []);

  const loadStores = useCallback(async () => {
    try {
      setStores(await getMyStores());
    } catch {
      /* ignore */
    }
  }, []);

  // Poll for the background batch anchored to the newly-selected store.
  const pollForNewBatch = useCallback((storeName: string) => {
    if (pollTimer.current) clearInterval(pollTimer.current);
    let tries = 0;
    pollTimer.current = setInterval(async () => {
      tries += 1;
      try {
        const res = await getLatestRecipes();
        if (res.store_name === storeName && res.recipes.length) {
          setRecipes(res.recipes);
          setGeneratedAt(res.generated_at);
          setBatchStore(res.store_name);
          if (pollTimer.current) clearInterval(pollTimer.current);
        }
      } catch {
        /* keep polling */
      }
      if (tries >= 20 && pollTimer.current) clearInterval(pollTimer.current);
    }, 4000);
  }, []);

  async function onSelectStore(id: number) {
    setSwitching(true);
    setError(null);
    try {
      const updated = await setDefaultStore(id);
      setStores(updated);
      setStoreSheet(false);
      const newName = (updated.find((s) => s.is_default) ?? updated[0])?.store.store_name;
      if (newName) pollForNewBatch(newName); // note stays until the new batch lands
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not switch stores.");
    } finally {
      setSwitching(false);
    }
  }

  // Initial load + optional auto-generate from ?generate=1.
  useEffect(() => {
    loadWeek();
    loadStores();
    const auto =
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).get("generate") === "1";
    if (auto) {
      window.history.replaceState({}, "", "/recipes");
      setWarming(false);
      generate();
    } else {
      loadLatest();
    }
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
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

      <div className="mb-5 flex items-center justify-between gap-2">
        <h1 className="text-2xl font-bold text-ink">Recipes</h1>
        {currentStoreName && (
          <button
            onClick={() => setStoreSheet(true)}
            className="flex shrink-0 items-center gap-1 rounded-full border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink active:scale-[.98]"
          >
            📍 <span className="max-w-[10rem] truncate">{currentStoreName}</span>
            <span className="text-ink-faint">▾</span>
          </button>
        )}
      </div>

      <ThisWeek
        week={week}
        cookingId={cookingId}
        buildingList={buildingList}
        onCooked={onCooked}
        onRemove={onRemove}
        onOpen={setSelected}
        onBuildList={onBuildList}
      />

      {/* Warm-load placeholder */}
      {warming && !recipes && !generating && (
        <div className="flex flex-col gap-4">
          {[0, 1, 2].map((i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      )}

      {/* Generate area */}
      {!recipes && !generating && !warming && (
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
          {stale ? (
            <div className="mb-3 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn">
              These were generated for {batchStore}. New {currentStoreName} recipes
              are on the way…
            </div>
          ) : (
            generatedAt && (
              <p className="mb-3 text-xs text-ink-faint">Generated {timeAgo(generatedAt)}</p>
            )
          )}
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
            className={`mt-4 flex h-12 w-full items-center justify-center rounded-2xl text-sm font-semibold active:scale-[.99] ${
              stale
                ? "bg-brand text-white shadow-lg shadow-brand/25"
                : "border border-hairline bg-surface text-ink"
            }`}
          >
            {stale ? `Get ${currentStoreName} recipes` : "Show me different options"}
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

      {storeSheet && (
        <StoreSheet
          stores={stores}
          currentId={currentStore?.store.id ?? null}
          switching={switching}
          onSelect={onSelectStore}
          onClose={() => setStoreSheet(false)}
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
