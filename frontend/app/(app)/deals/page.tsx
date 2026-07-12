"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useToast } from "@/components/ui/Toast";
import { getDeals, type Deal, type DealsState } from "@/lib/dealsApi";
import { CATEGORIES, categoryLabel } from "@/lib/categories";

const PER_PAGE = 20;

function num(v: string | number | null | undefined): number {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export default function DealsPage() {
  const toast = useToast();
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | null>(null);

  const [deals, setDeals] = useState<Deal[]>([]);
  const [count, setCount] = useState(0);
  const [dealsState, setDealsState] = useState<DealsState>("ready");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const reqId = useRef(0);

  // debounce the search box
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const fetchPage = useCallback(
    async (targetPage: number, replace: boolean) => {
      const id = ++reqId.current;
      if (replace) setLoading(true);
      else setLoadingMore(true);
      try {
        const res = await getDeals({
          search: search || undefined,
          category: category || undefined,
          page: targetPage,
          per_page: PER_PAGE,
        });
        if (id !== reqId.current) return; // stale response, ignore
        setCount(res.count);
        setPage(res.page);
        setDealsState(res.state);
        setDeals((prev) => (replace ? res.deals : [...prev, ...res.deals]));
      } catch {
        toast.error("Couldn't load deals.", () => fetchPage(targetPage, replace));
      } finally {
        if (id === reqId.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [search, category, toast],
  );

  useEffect(() => {
    fetchPage(1, true);
  }, [fetchPage]);

  const hasMore = deals.length < count;

  return (
    <div className="px-5 pt-8 pb-8">
      <header className="mb-4 flex items-center gap-2">
        <Link href="/" aria-label="Back" className="text-xl text-ink-soft">
          ‹
        </Link>
        <h1 className="text-2xl font-bold text-ink">Deals</h1>
      </header>

      <input
        value={searchInput}
        onChange={(e) => setSearchInput(e.target.value)}
        placeholder="Search deals…"
        className="h-12 w-full rounded-2xl border border-hairline bg-surface px-4 text-base outline-none focus:border-brand"
      />

      <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
        <Chip label="All" active={category === null} onClick={() => setCategory(null)} />
        {CATEGORIES.filter((c) => c !== "other").map((c) => (
          <Chip
            key={c}
            label={categoryLabel(c)}
            active={category === c}
            onClick={() => setCategory(c)}
          />
        ))}
      </div>

      {loading ? (
        <div className="mt-4 flex flex-col gap-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="skeleton h-16 rounded-2xl" />
          ))}
        </div>
      ) : deals.length === 0 && dealsState === "loading" ? (
        <div className="mt-6 rounded-2xl bg-warn-soft px-4 py-4 text-center text-sm text-warn">
          <div className="mb-1 text-2xl">🛒</div>
          Deals loading for your store — usually a few minutes. Check back shortly.
        </div>
      ) : deals.length === 0 && dealsState === "pending_source" ? (
        <div className="mt-6 rounded-2xl border border-hairline bg-surface px-4 py-4 text-center text-sm text-ink-soft">
          <div className="mb-1 text-2xl">📍</div>
          Deals coming soon for this store. We&apos;ve noted the request and are
          working on adding its weekly ad.
        </div>
      ) : deals.length === 0 ? (
        <p className="mt-10 text-center text-sm text-ink-soft">
          No deals match{search ? ` “${search}”` : ""}.
        </p>
      ) : (
        <>
          <p className="mt-4 text-xs text-ink-faint">{count} deals</p>
          <ul className="mt-2 flex flex-col gap-2">
            {deals.map((d) => (
              <li
                key={d.id}
                className="flex items-center gap-3 rounded-2xl border border-hairline bg-surface p-4"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">{d.product_name}</p>
                  {d.brand && <p className="truncate text-xs text-ink-faint">{d.brand}</p>}
                </div>
                <div className="shrink-0 text-right">
                  <p className="text-base font-bold text-ink">${num(d.sale_price).toFixed(2)}</p>
                  {d.regular_price != null && (
                    <p className="text-xs text-ink-faint line-through">
                      ${num(d.regular_price).toFixed(2)}
                    </p>
                  )}
                </div>
                {d.savings_pct != null && (
                  <span className="shrink-0 rounded-full bg-brand-soft px-2 py-1 text-[11px] font-bold text-brand-dark">
                    {num(d.savings_pct).toFixed(0)}% off
                  </span>
                )}
              </li>
            ))}
          </ul>

          {hasMore && (
            <button
              onClick={() => fetchPage(page + 1, false)}
              disabled={loadingMore}
              className="mt-4 flex h-12 w-full items-center justify-center rounded-2xl border border-hairline bg-surface text-sm font-semibold text-ink disabled:opacity-60"
            >
              {loadingMore ? "Loading…" : "Load more"}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function Chip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`h-9 shrink-0 rounded-full px-4 text-sm font-medium transition ${
        active ? "bg-brand text-white" : "border border-hairline bg-surface text-ink-soft"
      }`}
    >
      {label}
    </button>
  );
}
