"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useToast } from "@/components/ui/Toast";
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
import { getMe, type UserProfile } from "@/lib/userApi";
import { getTopDeals, type Deal } from "@/lib/dealsApi";
import { reportEvent } from "@/lib/eventsApi";
import { listPantry } from "@/lib/pantryApi";
import type { UserStore } from "@/lib/listTypes";
import type { PantryItem } from "@/lib/types";
import type { Recipe, WeekResponse } from "@/lib/recipeTypes";
import RecipeCard from "@/components/recipes/RecipeCard";
import RecipeSheet from "@/components/recipes/RecipeSheet";
import StoreSheet from "@/components/recipes/StoreSheet";
import ThisWeek from "@/components/recipes/ThisWeek";
import Confetti from "@/components/recipes/Confetti";
import SetupPanel from "@/components/recipes/SetupPanel";
import UpgradeCards from "@/components/recipes/UpgradeCards";
import GenerateMorePill from "@/components/recipes/GenerateMorePill";
import { type Pin } from "@/components/recipes/UseUpRow";

const STEPS = [
  "Reading your pantry…",
  "Checking this week's deals…",
  "Writing your recipes…",
];

const FIRST_BATCH_KEY = "pantryai:evt:first_batch_viewed";

/** Report first_batch_viewed once per browser (funnel counts distinct users). */
function maybeReportFirstBatch(count: number) {
  try {
    if (localStorage.getItem(FIRST_BATCH_KEY) === "1") return;
    localStorage.setItem(FIRST_BATCH_KEY, "1");
  } catch {
    /* still report */
  }
  reportEvent("first_batch_viewed", { count });
}

const TIER_RANK: Record<string, number> = { easy: 0, medium: 1, hard: 2 };
function tierRank(d: string | null): number {
  return TIER_RANK[(d ?? "").toLowerCase()] ?? 3;
}

const ALL_TIERS: Difficulty[] = ["easy", "medium", "hard"];
const DIFF_KEY = "pantryai:difficulties";
const PANTRY_MODE_KEY = "pantryai:pantryMode";

function loadPantryMode(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(PANTRY_MODE_KEY) === "1";
  } catch {
    return false;
  }
}

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
  const toast = useToast();
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
  const [pantryMode, setPantryMode] = useState(false);
  const [batchPantryMode, setBatchPantryMode] = useState(false);
  const [me, setMe] = useState<UserProfile | null>(null);
  // P40 B4: arriving from onboarding, the loading state IS the store's real
  // deals — prices on screen while the chef writes the first batch.
  const [welcomeDeals, setWelcomeDeals] = useState<Deal[]>([]);
  const [welcomeStore, setWelcomeStore] = useState<string | null>(null);
  const [fromWelcome, setFromWelcome] = useState(false);
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
  const pantryModeRef = useRef(false);
  useEffect(() => {
    pantryModeRef.current = pantryMode;
  }, [pantryMode]);

  // Hydrate the tier selection + pantry mode from localStorage once on mount.
  useEffect(() => {
    setDifficulties(loadDifficulties());
    setPantryMode(loadPantryMode());
  }, []);

  function togglePantryMode() {
    setPantryMode((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        try {
          localStorage.setItem(PANTRY_MODE_KEY, next ? "1" : "0");
        } catch {
          /* persistence is best-effort */
        }
      }
      return next;
    });
  }

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
    const activePantryMode = pantryModeRef.current;
    try {
      const res = await generateRecipes(
        activePins.filter((p) => p.kind !== "deal").map((p) => p.id),
        activeDirection,
        activeDiffs,
        activePins.filter((p) => p.kind === "deal").map((p) => p.id),
        activePantryMode,
      );
      setRecipes(res.recipes);
      maybeReportFirstBatch(res.recipes.length);
      setGeneratedAt(res.recipes[0]?.generated_at ?? new Date().toISOString());
      setBatchPins(activePins.map((p) => p.name));
      setBatchDirection(activeDirection || null);
      setBatchDifficulties(activeDiffs.length === 3 ? [] : activeDiffs);
      setBatchPantryMode(activePantryMode);
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
      const msg = e instanceof Error ? e.message : "Could not generate recipes.";
      setError(msg);
      // A structured 500 carries an error id (surfaced in msg) — toast it with a
      // retry so a dropped generation is never silent.
      toast.error(msg, () => generate());
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
        maybeReportFirstBatch(res.recipes.length);
        setGeneratedAt(res.generated_at);
        setBatchStore(res.store_name);
        setBatchPins(res.pinned ?? []);
        setBatchDirection(res.direction ?? null);
        setBatchDifficulties(res.difficulties ?? []);
        setBatchPantryMode(!!res.pantry_mode);
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
      const profile = await getMe();
      setMe(profile);
      if (profile.recipes_per_generation) setPerBatch(profile.recipes_per_generation);
    } catch {
      /* ignore — skeleton count falls back to default */
    }
  }, []);

  const pinsRef = useRef<Pin[]>([]);
  useEffect(() => {
    pinsRef.current = pins;
  }, [pins]);

  // Merge a freshly-detailed recipe into the shown batch (from the sheet's poll).
  const mergeRecipe = useCallback((fresh: Recipe) => {
    setRecipes((prev) =>
      prev
        ? prev.map((r) =>
            r.id === fresh.id ? { ...fresh, rating: r.rating ?? fresh.rating } : r,
          )
        : prev,
    );
    setSelected((prev) => (prev && prev.id === fresh.id ? fresh : prev));
  }, []);

  // B4 sync bug: concept cards show the model's estimate; once details land
  // (status='ready') the card must re-render with the CALCULATED nutrition and
  // reconciled cost. Poll /latest and merge until the eager slots are detailed
  // (lazy concepts stay concepts until tapped, so cap the poll window).
  const recipesRef = useRef<Recipe[] | null>(null);
  useEffect(() => {
    recipesRef.current = recipes;
  }, [recipes]);
  useEffect(() => {
    if (!generatedAt) return;
    if (pollTimer.current) clearInterval(pollTimer.current);
    if (!recipesRef.current?.some((r) => r.status === "concept")) return;
    let polls = 0;
    const MAX_POLLS = 24; // ~60s at 2.5s — covers eager detail generation
    pollTimer.current = setInterval(async () => {
      polls += 1;
      const anyConcept =
        recipesRef.current?.some((r) => r.status === "concept") ?? false;
      if (!anyConcept || polls > MAX_POLLS) {
        if (pollTimer.current) clearInterval(pollTimer.current);
        return;
      }
      try {
        const res = await getLatestRecipes();
        if (res.generated_at !== generatedAt) return; // different batch — ignore
        const byId = new Map(res.recipes.map((r) => [r.id, r]));
        setRecipes((prev) =>
          prev
            ? prev.map((r) => {
                const fresh = byId.get(r.id);
                return fresh && fresh.status === "ready"
                  ? { ...fresh, rating: r.rating ?? fresh.rating }
                  : r;
              })
            : prev,
        );
      } catch {
        /* keep polling */
      }
    }, 2500);
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [generatedAt]);

  // Setup-panel visibility (Prompt 29): the bottom "Generate more" pill appears
  // only once the top setup panel has scrolled out of view.
  const panelRef = useRef<HTMLDivElement | null>(null);
  const directionInputRef = useRef<HTMLInputElement | null>(null);
  const [panelVisible, setPanelVisible] = useState(true);
  useEffect(() => {
    const el = panelRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      ([entry]) => setPanelVisible(entry.isIntersecting),
      // Treat the panel as "gone" once it tucks behind the sticky tabs header
      // (~56px tall), so the pill appears right as the panel leaves view.
      { threshold: 0, rootMargin: "-56px 0px 0px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [tab]);

  function scrollToBrief() {
    panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    // focus the direction input once the smooth scroll has settled
    window.setTimeout(() => directionInputRef.current?.focus(), 350);
  }

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
    // "Cook with this sale" (P37): a deal pin arrives as a distinct 🏷️ chip.
    const dealId = params.get("pinDeal");
    if (dealId) {
      setPins((prev) => [
        ...prev,
        {
          id: Number(dealId),
          name: params.get("dealName") || "deal",
          kind: "deal",
          price: params.get("dealPrice"),
          priceUnit: params.get("dealUnit"),
        },
      ]);
    }
    // P40 B4: landing here from ZIP-first onboarding — show the chosen
    // store's real deals as the generation loading state.
    if (params.get("welcome") === "1") {
      setFromWelcome(true);
      setWelcomeStore(params.get("store"));
      getTopDeals()
        .then(setWelcomeDeals)
        .catch(() => {});
    }
    // ?tab=week lands on This Week; a pin or generate implies Discover.
    if (
      params.get("tab") === "week" && !pinId && !dealId &&
      params.get("generate") !== "1"
    ) {
      setTab("week");
    }
    if (params.get("generate") === "1") {
      window.history.replaceState({}, "", "/recipes");
      setTab("discover");
      setWarming(false);
      generate();
    } else {
      if (pinId || dealId) window.history.replaceState({}, "", "/recipes");
      loadLatest();
    }
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openRecipe = useCallback((r: Recipe) => {
    setSelected(r);
    reportEvent("recipe_opened", { recipe_id: r.id });
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
  if (batchPantryMode) labelParts.push("pantry mode");
  const batchLabel = labelParts.join(" · ");

  return (
    <div className="px-5 pt-8">
      {confetti && <Confetti onDone={() => setConfetti(false)} />}

      <h1 className="mb-4 text-2xl font-bold text-ink">Recipes</h1>

      {/* Tabs — sticky control cluster (store chip now lives in the setup panel) */}
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
      </div>

      {error && (
        <p className="mt-4 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">
          {error}
        </p>
      )}

      {/* ---------- DISCOVER ---------- */}
      {tab === "discover" && (
        <div className="mt-4">
          {/* TOP SETUP PANEL — all generation config lives here (scrolls with content) */}
          <SetupPanel
            ref={panelRef}
            storeName={currentStoreName}
            onOpenStore={() => setStoreSheet(true)}
            pins={pins}
            pantryItems={pantryItems}
            onAddPin={(p) =>
              setPins((prev) =>
                prev.length < 3 &&
                !prev.some((x) => x.id === p.id && (x.kind ?? "pantry") === (p.kind ?? "pantry"))
                  ? [...prev, p]
                  : prev,
              )
            }
            onRemovePin={(id, kind) =>
              setPins((prev) =>
                prev.filter((x) => !(x.id === id && (x.kind ?? "pantry") === (kind ?? "pantry"))),
              )
            }
            pantryMode={pantryMode}
            onTogglePantryMode={togglePantryMode}
            difficulties={difficulties}
            onToggleDifficulty={toggleDifficulty}
            direction={direction}
            onChangeDirection={setDirection}
            lastDirection={lastDirection}
            directionRef={directionInputRef}
            onGenerate={generate}
            generating={generating}
            stepText={STEPS[step]}
          />

          {/* Empty state: the panel IS the page, with a one-line explainer. */}
          {!recipes && !generating && !warming && (
            <p className="mt-3 text-center text-sm text-ink-soft">
              Tell the chef anything — or just hit Generate.
            </p>
          )}

          {/* Warm-load placeholder */}
          {warming && !recipes && !generating && (
            <div className="mt-4 flex flex-col gap-4">
              {Array.from({ length: perBatch }, (_, i) => (
                <SkeletonCard key={i} />
              ))}
            </div>
          )}

          {generating && (
            <div className="mt-4 flex flex-col gap-4">
              {/* P40 B4: onboarding loading state = the store's REAL deals. */}
              {fromWelcome && welcomeDeals.length > 0 && (
                <section className="rounded-2xl border border-brand/25 bg-brand-soft/60 p-4">
                  <h2 className="text-sm font-bold text-ink">
                    This week{welcomeStore ? ` at ${welcomeStore}` : ""} 🏷️
                  </h2>
                  <p className="mt-0.5 text-xs text-ink-soft">
                    Real flyer prices — your dinners are being built around these
                    right now.
                  </p>
                  <ul className="mt-2 divide-y divide-hairline/60">
                    {welcomeDeals.slice(0, 5).map((d) => (
                      <li key={d.id} className="flex items-center gap-3 py-2">
                        <span className="min-w-0 flex-1 truncate text-sm text-ink">
                          {d.product_name}
                        </span>
                        <span className="shrink-0 text-sm font-semibold text-ink">
                          ${Number(d.sale_price ?? 0).toFixed(2)}
                          {d.price_unit ? (
                            <span className="font-normal text-ink-faint">
                              /{d.price_unit}
                            </span>
                          ) : null}
                        </span>
                        {d.savings_pct != null && (
                          <span className="shrink-0 rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-bold text-brand-dark">
                            {Number(d.savings_pct).toFixed(0)}% off
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {Array.from({ length: perBatch }, (_, i) => (
                <SkeletonCard key={i} />
              ))}
            </div>
          )}

          {recipes && !generating && (
            <div className="mt-4">
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
                    onExpand={() => openRecipe(r)}
                  />
                ))}
              </div>
              {/* P40 B5: progressive upgrade cards — AFTER the batch, never
                  before it. One missing piece at a time, dismissible. */}
              <UpgradeCards
                me={me}
                pantryCount={pantryItems.length}
                onProfileSaved={setMe}
                onGenerate={() => {
                  window.scrollTo({ top: 0, behavior: "smooth" });
                  generate();
                }}
              />
            </div>
          )}

          {/* normal bottom padding (old sticky composer removed) */}
          <div className="h-6" />
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
              onOpen={openRecipe}
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
          onUpdate={mergeRecipe}
        />
      )}

      {/* Bottom "Generate more" pill — only once a batch exists AND the setup
          panel has scrolled out of view. */}
      {tab === "discover" && recipes && recipes.length > 0 && !panelVisible && (
        <GenerateMorePill
          onGenerateMore={generate}
          onEditBrief={scrollToBrief}
          generating={generating}
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
