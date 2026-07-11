"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useToast } from "@/components/ui/Toast";
import { getTopDeals, type Deal } from "@/lib/dealsApi";
import { getMyStores } from "@/lib/storesApi";
import { listPantry } from "@/lib/pantryApi";
import { getWeek } from "@/lib/recipeApi";
import { currentWeekStart } from "@/lib/week";
import type { PantryListResponse } from "@/lib/types";
import type { WeekResponse } from "@/lib/recipeTypes";
import { getMe, firstName, type UserProfile } from "@/lib/userApi";

const LAST_SCAN_KEY = "pantryai:lastScanAt";

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
  const [storeName, setStoreName] = useState<string | null>(null);
  const [week, setWeek] = useState<WeekResponse | null>(null);
  const [lastScan, setLastScan] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [meRes, dealsRes, pantryRes, storesRes, weekRes] = await Promise.all([
        getMe().catch(() => null),
        getTopDeals().catch(() => []),
        listPantry().catch(() => null),
        getMyStores().catch(() => []),
        getWeek(currentWeekStart()).catch(() => null),
      ]);
      setMe(meRes);
      setDeals(dealsRes);
      setPantry(pantryRes);
      setStoreName((storesRes.find((s) => s.is_default) ?? storesRes[0])?.store?.store_name ?? null);
      setWeek(weekRes);
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
          {/* Deals */}
          <section className="rounded-2xl border border-hairline bg-surface p-5">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-bold text-ink">
                This week&apos;s deals{storeName ? ` at ${storeName}` : ""}
              </h2>
              <Link href="/deals" className="text-sm font-semibold text-brand-dark">
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
                  </li>
                ))}
              </ul>
            )}
          </section>

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
                  <span
                    key={it.id}
                    className="rounded-full bg-warn-soft px-2.5 py-1 text-xs font-medium text-warn"
                  >
                    {it.name}
                  </span>
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
              href="/recipes"
              className="mt-4 flex h-11 w-full items-center justify-center rounded-xl border border-hairline bg-surface text-sm font-semibold text-ink active:scale-[.99]"
            >
              {savedRecipes.length === 0 ? "🍳 Get recipes" : "View recipes"}
            </Link>
          </section>
        </div>
      )}
    </div>
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
