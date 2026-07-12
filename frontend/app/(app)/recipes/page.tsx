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
import type { Difficulty } from "@/lib/recipeApi";
import { getMe } from "@/lib/userApi";
import { listPantry } from "@/lib/pantryApi";
import type { UserStore } from "@/lib/listTypes";
import type { PantryItem } from "@/lib/types";
import type { Recipe, WeekResponse } from "@/lib/recipeTypes";
import RecipeCard from "@/components/recipes/RecipeCard";
import RecipeSheet from "@/components/recipes/RecipeSheet";
import StoreSheet from "@/components/recipes/StoreSheet";
import ThisWeek from "@/components/recipes/ThisWeek";
import Confetti from "@/components/recipes/Confetti";
import Composer from "@/components/recipes/Composer";
import UseUpRow, { type Pin } from "@/components/recipes/UseUpRow";

const STEPS = [
  "Reading your pantry…",
  "Checking this week's deals…",
  "Writing your recipes…",
];

const TIER_RANK: Record<string, number> = { easy: 0, medium: 1, hard: 2 };
function tierRank(d: string | null): number {
  return TIER_RANK[(d ?? "").toLowerCase()] ?? 3;
}

const ALL_TIERS: Difficulty[] = ["easy", "medium", "hard"];
const DIFF_KEY = "pantryai:difficulties";

function orderTiers(ds: Difficulty[]): Difficulty[] {
  return ALL_TIERS.filter((t) => ds.includes(t));
}

function loadDifficulties(): Difficulty[] {
  if (typeof window === "undefined") return ALL_TIERS;
  try {
    const raw = JSON.parse(localStorage.getItem(DIFF_KEY) || "[]");
    const clean = orderTiers(
      (Array.isArray(raw) ? raw : []).filter((d): d is Difficulty =>
        ALL_TIERS.includes(d as Difficulty),
      ),
    );
    return clean.length ? clean : ALL_TIERS;
  } catch {
    return ALL_TIERS;
  }
}

/** Scope suffix for the batch label, null when all three tiers are in play. */
function scopeLabel(ds: string[]): string | null {
  const sel = orderTiers(ds.filter((d): d is Difficulty =>
    ALL_TIERS.includes(d as Difficulty),
  ));
  if (sel.length === 0 || sel.length === 3) return null;
  if (sel.length === 1) return `${sel[0]} only`;
  return sel.join("+");
}

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
  const [pins, setPins] = useState<Pin[]>([]);
  const [pantryItems, setPantryItems] = useState<PantryItem[]>([]);
  const [batchPins, setBatchPins] = useState<string[]>([]);
  const [tab, setTab] = useState<"discover" | "week">("discover");
  const [direction, setDirection] = useState("");
  const [batchDirection, setBatchDirection] = useState<string | null>(null);
  const [lastDirection, setLastDirection] = useState("");
  const [perBatch, setPerBatch] = useState(5);
  const [difficulties, setDifficulties] = useState<Difficulty[]>(ALL_TIERS);
  const [batchDifficulties, setBatchDifficulties] = useState<string[]>([]);
  const stepTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const directionRef = useRef("");
  useEffect(() => {
    directionRef.current = direction;
  }, [direction]);
  const difficultiesRef = useRef<Difficulty[]>(ALL_TIERS);
  useEffect(() => {
    difficultiesRef.current = difficulties;
  }, [difficulties]);

  // Hydrate the tier selection from localStorage once on mount.
  useEffect(() => {
    setDifficulties(loadDifficulties());
  }, []);

  function toggleDifficulty(d: Difficulty) {
    setDifficulties((prev) => {
      const next = prev.includes(d)
        ? prev.filter((x) => x !== d)
        : orderTiers([...prev, d]);
      const final = next.length ? next : prev; // never empty
      if (typeof window !== "undefined") {
        localStorage.setItem(DIFF_KEY, JSON.stringify(final));
      }
      return final;
    });
  }

  function selectTab(t: "discover" | "week") {
    setTab(t);
    if (typeof window !== "undefined") {
      const url = t === "week" ? "/recipes?tab=week" : "/recipes";
      window.history.replaceState({}, "", url);
    }
  }

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
    const activePins = pinsRef.current;
    const activeDirection = directionRef.current.trim();
    const activeDiffs = difficultiesRef.current;
    try {
      const res = await generateRecipes(
        activePins.map((p) => p.id),
        activeDirection,
        activeDiffs,
      );
      setRecipes(res.recipes);
      setGeneratedAt(res.recipes[0]?.generated_at ?? new Date().toISOString());
      setBatchPins(activePins.map((p) => p.name));
      setBatchDirection(activeDirection || null);
      setBatchDifficulties(activeDiffs.length === 3 ? [] : activeDiffs);
      setPins([]); // pins satisfied — clear them
      // Direction is ephemeral: clear the input, offer it back as a placeholder.
      if (activeDirection) setLastDirection(activeDirection);
      setDirection("");
      // Fresh batch is anchored to the current default store.
      setStores((prev) => {
        const name = (prev.find((s) => s.is_default) ?? prev[0])?.store.store_name;
        setBatchStore(name ?? null);
        return prev;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate recipes.");
      // keep pins + direction on failure so the user can retry
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
        setBatchPins(res.pinned ?? []);
        setBatchDirection(res.direction ?? null);
        setBatchDifficulties(res.difficulties ?? []);
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

  const loadPantry = useCallback(async () => {
    try {
      const data = await listPantry();
      setPantryItems(data.categories.flatMap((c) => c.items));
    } catch {
      /* ignore */
    }
  }, []);

  const loadProfile = useCallback(async () => {
    try {
      const me = await getMe();
      if (me.recipes_per_generation) setPerBatch(me.recipes_per_generation);
    } catch {
      /* ignore — skeleton count falls back to default */
    }
  }, []);

  const pinsRef = useRef<Pin[]>([]);
  useEffect(() => {
    pinsRef.current = pins;
  }, [pins]);

  async function onSelectStore(id: number) {
    setSwitching(true);
    setError(null);
    try {
      const updated = await setDefaultStore(id);
      setStores(updated);
      setStoreSheet(false);
      // Prompt 27: switching stores no longer auto-generates a batch. The
      // staleness note + emphasized Generate button cover intent.
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not switch stores.");
    } finally {
      setSwitching(false);
    }
  }

  // Initial load + optional auto-generate (?generate=1) or pre-pinned item (?pin).
  useEffect(() => {
    loadWeek();
    loadStores();
    loadPantry();
    loadProfile();
    const params =
      typeof window !== "undefined"
        ? new URLSearchParams(window.location.search)
        : new URLSearchParams();
    const pinId = params.get("pin");
    if (pinId) {
      setPins([{ id: Number(pinId), name: params.get("name") || "item" }]);
    }
    // ?tab=week lands on This Week; a pin or generate implies Discover.
    if (params.get("tab") === "week" && !pinId && params.get("generate") !== "1") {
      setTab("week");
    }
    if (params.get("generate") === "1") {
      window.history.replaceState({}, "", "/recipes");
      setTab("discover");
      setWarming(false);
      generate();
    } else {
      if (pinId) window.history.replaceState({}, "", "/recipes");
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

  const weekCount = week?.recipes.length ?? 0;

  const labelParts: string[] = [];
  if (generatedAt)
    labelParts.push(`Generated ${timeAgo(generatedAt)}${batchStore ? ` at ${batchStore}` : ""}`);
  else if (batchStore) labelParts.push(`At ${batchStore}`);
  if (batchPins.length) labelParts.push(`built around: ${batchPins.join(", ")}`);
  if (batchDirection) labelParts.push(`direction: ‘${batchDirection}’`);
  const scope = scopeLabel(batchDifficulties);
  if (scope) labelParts.push(scope);
  const batchLabel = labelParts.join(" · ");

  return (
    <div className="px-5 pt-8">
      {confetti && <Confetti onDone={() => setConfetti(false)} />}

      <h1 className="mb-4 text-2xl font-bold text-ink">Recipes</h1>

      {/* Tabs + (on Discover) store chip — sticky control cluster */}
      <div className="sticky top-0 z-20 -mx-5 bg-canvas/95 px-5 pb-3 pt-1 backdrop-blur">
        <div className="flex rounded-full border border-hairline bg-surface p-1">
          <button
            onClick={() => selectTab("discover")}
            className={`flex-1 rounded-full py-2 text-sm font-semibold transition ${
              tab === "discover" ? "bg-brand text-white" : "text-ink-soft"
            }`}
          >
            Discover
          </button>
          <button
            onClick={() => selectTab("week")}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-full py-2 text-sm font-semibold transition ${
              tab === "week" ? "bg-brand text-white" : "text-ink-soft"
            }`}
          >
            This week
            {weekCount > 0 && (
              <span
                className={`flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-xs font-bold ${
                  tab === "week" ? "bg-white/25 text-white" : "bg-brand-soft text-brand-dark"
                }`}
              >
                {weekCount}
              </span>
            )}
          </button>
        </div>

        {tab === "discover" && currentStoreName && (
          <div className="mt-2 flex justify-end">
            <button
              onClick={() => setStoreSheet(true)}
              className="flex shrink-0 items-center gap-1 rounded-full border border-hairline bg-surface px-3 py-1.5 text-sm font-medium text-ink active:scale-[.98]"
            >
              📍 <span className="max-w-[10rem] truncate">{currentStoreName}</span>
              <span className="text-ink-faint">▾</span>
            </button>
          </div>
        )}
      </div>

      {error && (
        <p className="mt-4 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">
          {error}
        </p>
      )}

      {/* ---------- DISCOVER ---------- */}
      {tab === "discover" && (
        <div className="mt-4">
          {!generating && (
            <UseUpRow
              pins={pins}
              pantryItems={pantryItems}
              onAdd={(p) =>
                setPins((prev) =>
                  prev.length < 3 && !prev.some((x) => x.id === p.id) ? [...prev, p] : prev,
                )
              }
              onRemove={(id) => setPins((prev) => prev.filter((x) => x.id !== id))}
            />
          )}

          {/* Warm-load placeholder */}
          {warming && !recipes && !generating && (
            <div className="flex flex-col gap-4">
              {Array.from({ length: perBatch }, (_, i) => (
                <SkeletonCard key={i} />
              ))}
            </div>
          )}

          {/* Generate area — the composer below is the sole trigger */}
          {!recipes && !generating && !warming && (
            <div className="rounded-2xl border border-hairline bg-surface p-6 text-center">
              <p className="text-base font-semibold text-ink">Tonight&apos;s dinner, sorted</p>
              <p className="mt-1 text-sm text-ink-soft">
                {perBatch} options built from your pantry and this week&apos;s deals. Add a
                direction below, or just tap ✨ Generate.
              </p>
            </div>
          )}

          {generating && (
            <div>
              <div className="flex flex-col gap-4">
                {Array.from({ length: perBatch }, (_, i) => (
                  <SkeletonCard key={i} />
                ))}
              </div>
            </div>
          )}

          {recipes && !generating && (
            <div>
              {stale ? (
                <div className="mb-3 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn">
                  These were built for {batchStore}. Tap ✨ Generate for fresh{" "}
                  {currentStoreName} recipes.
                </div>
              ) : (
                batchLabel && (
                  <p className="mb-3 text-xs text-ink-faint">{batchLabel}</p>
                )
              )}
              <div className="flex flex-col gap-4">
                {[...recipes]
                  .sort((a, b) => tierRank(a.difficulty) - tierRank(b.difficulty))
                  .map((r) => (
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
            </div>
          )}

          {/* room so the last card clears the sticky composer */}
          <div className="h-32" />
        </div>
      )}

      {/* ---------- THIS WEEK ---------- */}
      {tab === "week" && (
        <div className="mt-4">
          {weekCount > 0 ? (
            <ThisWeek
              week={week}
              cookingId={cookingId}
              buildingList={buildingList}
              onCooked={onCooked}
              onRemove={onRemove}
              onOpen={setSelected}
              onBuildList={onBuildList}
            />
          ) : (
            <div className="rounded-2xl border border-hairline bg-surface p-8 text-center">
              <div className="text-4xl" aria-hidden>🍽️</div>
              <p className="mt-3 text-base font-semibold text-ink">Nothing saved yet</p>
              <p className="mt-1 text-sm text-ink-soft">
                Discover recipes and tap <span className="font-semibold">Save</span> to add them to
                this week&apos;s plan.
              </p>
              <button
                onClick={() => selectTab("discover")}
                className="mt-5 flex h-12 w-full items-center justify-center rounded-2xl bg-brand text-sm font-semibold text-white active:scale-[.99]"
              >
                Discover recipes
              </button>
            </div>
          )}
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

      {tab === "discover" && (
        <Composer
          value={direction}
          onChange={setDirection}
          onGenerate={generate}
          generating={generating}
          stepText={STEPS[step]}
          lastDirection={lastDirection}
          difficulties={difficulties}
          onToggleDifficulty={toggleDifficulty}
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
