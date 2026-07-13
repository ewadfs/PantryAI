"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useToast } from "@/components/ui/Toast";
import { getDealsState, getTopDeals, type Deal } from "@/lib/dealsApi";
import { getMyStores, setDefaultStore } from "@/lib/storesApi";
import StoreChip from "@/components/stores/StoreChip";
import StoreSheet from "@/components/recipes/StoreSheet";
import type { UserStore } from "@/lib/listTypes";
import { listPantry } from "@/lib/pantryApi";
import { getWeek } from "@/lib/recipeApi";
import { currentWeekStart } from "@/lib/week";
import type { PantryListResponse } from "@/lib/types";
import type { WeekResponse } from "@/lib/recipeTypes";
import { getMe, firstName, type UserProfile } from "@/lib/userApi";
import { getSavings, type SavingsResponse } from "@/lib/statsApi";

const LAST_SCAN_KEY = "pantryai:lastScanAt";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function joinedMonth(iso: string | null | undefined): string {
  if (!iso) return "you joined";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "you joined";
  return `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function daysAgo(iso: string | null): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.floor((Date.now() - then) / 86_400_000);
}

function num(v: string | number | null | undefined): number {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export default function HomePage() {
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [me, setMe] = useState<UserProfile | null>(null);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [pantry, setPantry] = useState<PantryListResponse | null>(null);
  const [stores, setStores] = useState<UserStore[]>([]);
  const [storeSheet, setStoreSheet] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [circularOn, setCircularOn] = useState(false);
  const [week, setWeek] = useState<WeekResponse | null>(null);
  const [savings, setSavings] = useState<SavingsResponse | null>(null);
  const [lastScan, setLastScan] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [meRes, dealsRes, pantryRes, storesRes, weekRes, savingsRes, stateRes] =
        await Promise.all([
          getMe().catch(() => null),
          getTopDeals().catch(() => []),
          listPantry().catch(() => null),
          getMyStores().catch(() => []),
          getWeek(currentWeekStart()).catch(() => null),
          getSavings().catch(() => null),
          getDealsState().catch(() => null),
        ]);
      setMe(meRes);
      setDeals(dealsRes);
      setPantry(pantryRes);
      setStores(storesRes);
      setWeek(weekRes);
      setSavings(savingsRes);
      setCircularOn(stateRes?.circular_viewer ?? false);
    } catch {
      toast.error("Couldn't load your home screen.", load);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
    if (typeof window !== "undefined") setLastScan(localStorage.getItem(LAST_SCAN_KEY));
  }, [load]);

  const name = firstName(me);
  const currentStore = stores.find((s) => s.is_default) ?? stores[0] ?? null;
  const storeName = currentStore?.store?.store_name ?? null;

  async function onSelectStore(id: number) {
    setSwitching(true);
    try {
      const updated = await setDefaultStore(id);
      setStores(updated);
      setStoreSheet(false);
      // Deals follow the anchored store — refetch the card (P37 A; recipe
      // regeneration stays manual per the P27 debounce rules).
      const [fresh, st] = await Promise.all([
        getTopDeals().catch(() => []),
        getDealsState().catch(() => null),
      ]);
      setDeals(fresh);
      if (st) setCircularOn(st.circular_viewer);
    } catch {
      toast.error("Could not switch stores.");
    } finally {
      setSwitching(false);
    }
  }

  if (loading) return <HomeSkeleton />;

  const itemCount = pantry?.count ?? 0;
  const firstRun = itemCount === 0;
  const useSoon = pantry
    ? pantry.categories.flatMap((g) => g.items).filter((i) => i.use_soon)
    : [];
  const savedRecipes = week?.recipes ?? [];
  const nextUncooked = savedRecipes.find((w) => !w.is_cooked)?.recipe;
  const scanned = daysAgo(lastScan);

  return (
    <div className="px-5 pt-8">
      <header className="mb-6">
        <p className="text-sm text-ink-soft">{greeting()},</p>
        <h1 className="text-2xl font-bold text-ink">{name} 👋</h1>
      </header>

      {firstRun ? (
        <Onboarding />
      ) : (
        <div className="flex flex-col gap-4">
          {/* Deals — header carries the SAME store chip as the Recipes setup
              panel (P37 A): one component, two homes. */}
          <section className="rounded-2xl border border-hairline bg-surface p-5">
            <div className="flex items-center justify-between gap-2">
              <h2 className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-base font-bold text-ink">
                <span className="shrink-0">This week&apos;s deals</span>
                {storeName && (
                  <>
                    <span className="shrink-0 font-normal text-ink-soft">at</span>
                    <StoreChip storeName={storeName} onOpen={() => setStoreSheet(true)} />
                  </>
                )}
              </h2>
              <Link href="/deals" className="shrink-0 text-sm font-semibold text-brand-dark">
                See all
              </Link>
            </div>
            {deals.length === 0 ? (
              <p className="mt-3 text-sm text-ink-soft">No deals available right now.</p>
            ) : (
              <ul className="mt-3 divide-y divide-hairline">
                {deals.slice(0, 5).map((d) => (
                  <li key={d.id} className="flex items-center gap-3 py-2.5">
                    <span className="min-w-0 flex-1 truncate text-sm text-ink">
                      {d.product_name}
                    </span>
                    <span className="shrink-0 text-sm font-semibold text-ink">
                      ${num(d.sale_price).toFixed(2)}
                    </span>
                    {d.savings_pct != null && (
                      <span className="shrink-0 rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-bold text-brand-dark">
                        {num(d.savings_pct).toFixed(0)}% off
                      </span>
                    )}
                    <Link
                      href={`/recipes?pinDeal=${d.id}&dealName=${encodeURIComponent(d.product_name)}&dealPrice=${encodeURIComponent(String(d.sale_price))}${d.price_unit ? `&dealUnit=${encodeURIComponent(d.price_unit)}` : ""}`}
                      aria-label={`Cook with ${d.product_name}`}
                      className="shrink-0 rounded-full bg-warn-soft px-2 py-1 text-[11px] font-semibold text-warn active:scale-95"
                    >
                      🍳 Cook with this
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {circularOn && (
              <Link
                href="/circular/default"
                className="mt-3 inline-block text-sm font-semibold text-brand-dark"
              >
                📰 View circular →
              </Link>
            )}
          </section>

          {/* Savings */}
          <SavingsCard savings={savings} joinedIso={me?.created_at ?? null} />

          {/* Kitchen status */}
          <section className="rounded-2xl border border-hairline bg-surface p-5">
            <h2 className="text-base font-bold text-ink">Your kitchen</h2>
            <p className="mt-1 text-sm text-ink-soft">
              {itemCount} item{itemCount === 1 ? "" : "s"}
              {scanned != null &&
                ` · last scanned ${scanned === 0 ? "today" : `${scanned} day${scanned === 1 ? "" : "s"} ago`}`}
            </p>
            {useSoon.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {useSoon.slice(0, 6).map((it) => (
                  <Link
                    key={it.id}
                    href={`/recipes?pin=${it.id}&name=${encodeURIComponent(it.name ?? "")}`}
                    className="rounded-full bg-warn-soft px-2.5 py-1 text-xs font-medium text-warn active:scale-95"
                  >
                    🍳 {it.name}
                  </Link>
                ))}
              </div>
            )}
            <div className="mt-4 flex gap-3">
              <Link
                href="/scan"
                className="flex h-11 flex-1 items-center justify-center rounded-xl bg-brand text-sm font-semibold text-white active:scale-[.99]"
              >
                📸 Scan
              </Link>
              <Link
                href="/pantry"
                className="flex h-11 flex-1 items-center justify-center rounded-xl border border-hairline bg-surface text-sm font-semibold text-ink active:scale-[.99]"
              >
                Pantry
              </Link>
            </div>
          </section>

          {/* This week */}
          <section className="rounded-2xl border border-hairline bg-surface p-5">
            <h2 className="text-base font-bold text-ink">This week</h2>
            {savedRecipes.length === 0 ? (
              <p className="mt-1 text-sm text-ink-soft">No recipes saved yet.</p>
            ) : (
              <p className="mt-1 text-sm text-ink-soft">
                {savedRecipes.length} recipe{savedRecipes.length === 1 ? "" : "s"} saved
                {nextUncooked ? ` · next up: ${nextUncooked.title}` : " · all cooked ✅"}
              </p>
            )}
            <Link
              href={savedRecipes.length === 0 ? "/recipes" : "/recipes?tab=week"}
              className="mt-4 flex h-11 w-full items-center justify-center rounded-xl border border-hairline bg-surface text-sm font-semibold text-ink active:scale-[.99]"
            >
              {savedRecipes.length === 0 ? "🍳 Get recipes" : "View recipes"}
            </Link>
          </section>
        </div>
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

function SavingsCard({
  savings,
  joinedIso,
}: {
  savings: SavingsResponse | null;
  joinedIso: string | null;
}) {
  // Empty state: no completed trips yet — no fake zeros dressed as data.
  if (!savings || savings.all_time.trips === 0) {
    return (
      <section className="rounded-2xl border border-hairline bg-surface p-5">
        <h2 className="text-base font-bold text-ink">💰 Your savings</h2>
        <p className="mt-2 text-sm text-ink-soft">
          Your savings show up here after your first shopping trip ✔
        </p>
      </section>
    );
  }

  const a = savings.all_time;
  const deals = num(a.deal_savings);
  const pantry = num(a.pantry_value_used);
  const total = deals + pantry;
  const trips = a.trips;
  const meals = savings.cooked_recipe_count;

  const m = savings.this_month;
  const monthTotal = num(m.deal_savings) + num(m.pantry_value_used);
  const showMonth = m.trips > 0 && total - monthTotal >= 1;

  return (
    <section className="rounded-2xl border border-brand/30 bg-brand-soft p-5">
      <p className="text-sm font-medium text-brand-dark">
        💰 Saved <span className="text-lg font-bold">${total.toFixed(2)}</span> since{" "}
        {joinedMonth(joinedIso)}
      </p>
      <p className="mt-2 text-sm text-brand-dark/90">
        🏷️ ${deals.toFixed(2)} from deals · 🏠 ${pantry.toFixed(2)} of pantry put to work
      </p>
      <p className="mt-0.5 text-sm text-brand-dark/90">
        {trips} trip{trips === 1 ? "" : "s"} · {meals} meal{meals === 1 ? "" : "s"} cooked
      </p>
      {showMonth && (
        <p className="mt-2 text-xs font-medium text-brand-dark/80">
          This month: ${monthTotal.toFixed(2)} saved
        </p>
      )}
    </section>
  );
}

function Onboarding() {
  const steps = [
    { icon: "📸", title: "Scan your kitchen", body: "Snap your fridge and shelves — we detect what you have." },
    { icon: "🍳", title: "Get recipes", body: "Three dinners built from your pantry and this week's deals." },
    { icon: "🛒", title: "Build a list", body: "One smart shopping list for only what you still need." },
  ];
  return (
    <div className="rounded-2xl border border-hairline bg-surface p-6">
      <h2 className="text-lg font-bold text-ink">Welcome to PantryAI</h2>
      <p className="mt-1 text-sm text-ink-soft">Three steps to smarter dinners.</p>
      <ol className="mt-5 flex flex-col gap-4">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-brand-soft text-xl">
              {s.icon}
            </span>
            <div>
              <p className="font-semibold text-ink">
                {i + 1}. {s.title}
              </p>
              <p className="text-sm text-ink-soft">{s.body}</p>
            </div>
          </li>
        ))}
      </ol>
      <Link
        href="/scan"
        className="mt-6 flex h-14 w-full items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white shadow-lg shadow-brand/25 active:scale-[.99]"
      >
        📸 Scan my kitchen
      </Link>
    </div>
  );
}

function HomeSkeleton() {
  return (
    <div className="px-5 pt-8">
      <div className="skeleton mb-1 h-4 w-24 rounded" />
      <div className="skeleton mb-6 h-7 w-40 rounded" />
      <div className="flex flex-col gap-4">
        {[0, 1, 2].map((i) => (
          <div key={i} className="rounded-2xl border border-hairline bg-surface p-5">
            <div className="skeleton h-5 w-40 rounded" />
            <div className="skeleton mt-3 h-4 w-full rounded" />
            <div className="skeleton mt-2 h-4 w-2/3 rounded" />
            <div className="skeleton mt-4 h-11 w-full rounded-xl" />
          </div>
        ))}
      </div>
    </div>
  );
}
