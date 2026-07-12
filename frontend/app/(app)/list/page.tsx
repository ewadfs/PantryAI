"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { categoryLabel } from "@/lib/categories";
import { formatWeekRange } from "@/lib/week";
import {
  addListItem,
  completeList,
  getCurrentList,
  getMyStores,
  setItemChecked,
} from "@/lib/listApi";
import { setDefaultStore } from "@/lib/storesApi";
import { buildShoppingList } from "@/lib/recipeApi";
import { comparePricesForList, type PriceCompareResponse } from "@/lib/pricesApi";
import { getSavings, type SavingsResponse } from "@/lib/statsApi";
import type { CurrentList, ShoppingItem } from "@/lib/listTypes";
import ListRow from "@/components/list/ListRow";
import Confetti from "@/components/recipes/Confetti";

const MILESTONES = [25, 50, 100];

function num(v: string | number | null | undefined): number {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export default function ListPage() {
  const [list, setList] = useState<CurrentList | null>(null);
  const [storeName, setStoreName] = useState<string | null>(null);
  const [compare, setCompare] = useState<PriceCompareResponse | null>(null);
  const [switchingTo, setSwitchingTo] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Set<number>>(new Set());
  const [addOpen, setAddOpen] = useState(false);
  const [cartOpen, setCartOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [completed, setCompleted] = useState<number | null>(null);
  const [savings, setSavings] = useState<SavingsResponse | null>(null);
  const [milestone, setMilestone] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [cur, stores] = await Promise.all([getCurrentList(), getMyStores().catch(() => [])]);
      setList(cur);
      const def = stores.find((s) => s.is_default) ?? stores[0];
      setStoreName(def?.store?.store_name ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load your list.");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Cross-store price comparison for the current list's needed items.
  useEffect(() => {
    const id = list?.id;
    if (id == null) return;
    let cancelled = false;
    comparePricesForList(id)
      .then((res) => {
        if (!cancelled) setCompare(res);
      })
      .catch(() => {
        /* comparison is best-effort */
      });
    return () => {
      cancelled = true;
    };
  }, [list?.id]);

  async function switchStore(storeLocationId: number) {
    if (!list?.week_start) return;
    setSwitchingTo(storeLocationId);
    try {
      await setDefaultStore(storeLocationId);
      await buildShoppingList(list.week_start);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't switch stores.");
    } finally {
      setSwitchingTo(null);
    }
  }

  const allItems = useMemo<ShoppingItem[]>(
    () => (list ? list.categories.flatMap((c) => c.items) : []),
    [list],
  );
  const checked = allItems.filter((i) => i.is_checked);
  const unpriced = allItems.filter((i) => i.price == null).length;
  const uncheckedGroups = useMemo(
    () =>
      (list?.categories ?? [])
        .map((g) => ({ category: g.category, items: g.items.filter((i) => !i.is_checked) }))
        .filter((g) => g.items.length > 0),
    [list],
  );

  function patchItem(itemId: number, patch: Partial<ShoppingItem>) {
    setList((prev) =>
      prev
        ? {
            ...prev,
            categories: prev.categories.map((g) => ({
              ...g,
              items: g.items.map((i) => (i.id === itemId ? { ...i, ...patch } : i)),
            })),
          }
        : prev,
    );
  }

  async function onToggle(item: ShoppingItem, isChecked: boolean) {
    if (!list) return;
    patchItem(item.id, { is_checked: isChecked }); // optimistic
    setPending((s) => new Set(s).add(item.id));
    try {
      await setItemChecked(list.id, item.id, isChecked);
    } catch {
      patchItem(item.id, { is_checked: !isChecked }); // revert
    } finally {
      setPending((s) => {
        const n = new Set(s);
        n.delete(item.id);
        return n;
      });
    }
  }

  async function addExtra(name: string, qty: string) {
    if (!list || !name.trim()) return;
    await addListItem(list.id, { display_name: name.trim(), quantity: qty.trim() || null });
    await load();
  }

  async function onComplete() {
    if (!list) return;
    setCompleting(true);
    try {
      const res = await completeList(list.id);
      setCompleted(res.items_added_to_pantry);
      // Pull the fresh scoreboard for the trip line + milestone confetti.
      try {
        const s = await getSavings();
        setSavings(s);
        const after = num(s.all_time.deal_savings);
        const before = after - num(s.last_trip?.deal_savings);
        setMilestone(MILESTONES.some((m) => before < m && after >= m));
      } catch {
        /* scoreboard is a nice-to-have; never block completion */
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not complete the list.");
    } finally {
      setCompleting(false);
      setConfirmOpen(false);
    }
  }

  // ---- success screen ----
  if (completed != null) {
    const tripSavings = num(savings?.last_trip?.deal_savings);
    const runningTotal =
      num(savings?.all_time.deal_savings) + num(savings?.all_time.pantry_value_used);
    return (
      <div className="flex min-h-[70vh] flex-col items-center justify-center px-6 text-center">
        {milestone && <Confetti onDone={() => setMilestone(false)} />}
        <span className="flex h-20 w-20 items-center justify-center rounded-full bg-brand text-white">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M20 6 9 17l-5-5" />
          </svg>
        </span>
        <h1 className="mt-6 text-2xl font-bold text-ink">All done!</h1>
        <p className="mt-1 text-sm text-ink-soft">
          {completed} item{completed === 1 ? "" : "s"} added to your pantry.
        </p>
        {tripSavings > 0 && (
          <div className="mt-5 w-full max-w-xs rounded-2xl border border-brand/30 bg-brand-soft p-4">
            <p className="text-base font-bold text-brand-dark">
              This trip: ${tripSavings.toFixed(2)} in deal savings 🏷️
            </p>
            <p className="mt-1 text-sm text-brand-dark/90">
              ${runningTotal.toFixed(2)} saved with PantryAI so far
            </p>
          </div>
        )}
        <p className="mt-4 max-w-xs text-sm text-ink-soft">
          📸 Scan your kitchen after unpacking for the best results.
        </p>
        <div className="mt-8 flex w-full max-w-xs flex-col gap-3">
          <Link
            href="/scan"
            className="flex h-14 items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white shadow-lg shadow-brand/25 active:scale-[.99]"
          >
            Scan your kitchen
          </Link>
          <Link
            href="/"
            className="flex h-14 items-center justify-center rounded-2xl border border-hairline bg-surface text-base font-semibold text-ink active:scale-[.99]"
          >
            Back home
          </Link>
        </div>
      </div>
    );
  }

  // ---- loading ----
  if (!loaded) {
    return <p className="px-5 pt-10 text-center text-sm text-ink-soft">Loading your list…</p>;
  }

  // ---- empty state ----
  if (!list) {
    return (
      <div className="flex min-h-[70vh] flex-col items-center justify-center px-6 text-center">
        <div className="text-5xl" aria-hidden>🛒</div>
        <h1 className="mt-4 text-xl font-bold text-ink">No shopping list yet</h1>
        <p className="mt-1 max-w-xs text-sm text-ink-soft">
          Save some recipes to this week, then build a list from what you need.
        </p>
        <Link
          href="/recipes"
          className="mt-6 flex h-14 items-center justify-center rounded-2xl bg-brand px-8 text-base font-semibold text-white active:scale-[.99]"
        >
          Save some recipes first
        </Link>
      </div>
    );
  }

  const knownCost = num(list.total_known_cost);
  const dealSavings = num(list.deal_savings);
  const itemCount = list.item_count ?? allItems.length;

  return (
    <div className="px-5 pt-8 pb-28">
      <header>
        <h1 className="text-2xl font-bold text-ink">
          Shopping at {storeName ?? "your store"}
        </h1>
        {list.week_start && (
          <p className="mt-1 text-sm text-ink-soft">{formatWeekRange(list.week_start)}</p>
        )}
      </header>

      {/* summary */}
      <div className="mt-4 rounded-2xl border border-hairline bg-surface p-4">
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-ink-soft">
            {itemCount} item{itemCount === 1 ? "" : "s"}
          </span>
          <span className="text-lg font-bold text-ink">
            Known cost: ${knownCost.toFixed(2)}
          </span>
        </div>
        {unpriced > 0 && (
          <p className="mt-1 text-sm text-ink-soft">
            + {unpriced} item{unpriced === 1 ? "" : "s"} unpriced
          </p>
        )}
        {dealSavings > 0 && (
          <p className="mt-2 inline-flex rounded-full bg-brand-soft px-3 py-1 text-sm font-semibold text-brand-dark">
            🏷️ Deal savings: ${dealSavings.toFixed(2)}
          </p>
        )}
      </div>

      {list.store_name && storeName && list.store_name !== storeName && (
        <p className="mt-3 rounded-xl bg-warn-soft px-4 py-2.5 text-sm text-warn">
          Priced at {list.store_name} — rebuild to re-price at {storeName}.
        </p>
      )}

      {error && (
        <p className="mt-4 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">
          {error}
        </p>
      )}

      {/* store price comparison */}
      {compare && compare.needed_count > 0 && compare.stores.length > 1 && (
        <section className="mt-5">
          <div className="flex items-baseline justify-between px-1">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-faint">
              Compare stores
            </h2>
            <span className="text-[11px] text-ink-faint">
              known deals · {compare.needed_count} to buy
            </span>
          </div>
          <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
            {[...compare.stores]
              .sort((a, b) => Number(b.is_default) - Number(a.is_default))
              .map((s) => {
                const name = s.chain_name ?? s.store_name ?? "Store";
                const busy = switchingTo === s.store_id;
                return (
                  <button
                    key={s.store_id}
                    disabled={s.is_default || switchingTo != null}
                    onClick={() => switchStore(s.store_id)}
                    className={`w-36 shrink-0 rounded-2xl border p-3 text-left ${
                      s.is_default
                        ? "border-brand bg-brand-soft"
                        : "border-hairline bg-surface active:scale-[.98]"
                    }`}
                  >
                    <p
                      className={`truncate text-sm font-semibold ${
                        s.is_default ? "text-brand-dark" : "text-ink"
                      }`}
                    >
                      {name}
                    </p>
                    <p className="mt-1 text-base font-bold text-ink">
                      {s.priced_count > 0 ? `$${num(s.known_cost_sum).toFixed(2)}` : "—"}
                    </p>
                    <p className="text-[11px] text-ink-faint">
                      {s.priced_count} of {s.total_count} priced
                    </p>
                    <p className="mt-1 text-[11px] font-medium text-brand-dark">
                      {busy
                        ? "Switching…"
                        : s.is_default
                          ? "This week"
                          : "Tap to switch"}
                    </p>
                  </button>
                );
              })}
          </div>
          <p className="mt-1 px-1 text-[11px] text-ink-faint">
            Known costs from current flyers. “—” means no deal listed there.
          </p>
        </section>
      )}

      {/* grouped items */}
      {uncheckedGroups.map((g) => (
        <section key={g.category} className="mt-6">
          <h2 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
            {categoryLabel(g.category)}
          </h2>
          <div className="divide-y divide-hairline overflow-hidden rounded-2xl border border-hairline bg-surface">
            {g.items.map((it) => (
              <ListRow
                key={it.id}
                item={it}
                pending={pending.has(it.id)}
                onToggle={(c) => onToggle(it, c)}
              />
            ))}
          </div>
        </section>
      ))}

      {/* add extra */}
      <AddExtra open={addOpen} setOpen={setAddOpen} onAdd={addExtra} />

      {/* in cart */}
      {checked.length > 0 && (
        <section className="mt-6">
          <button
            onClick={() => setCartOpen((v) => !v)}
            className="mb-2 flex w-full items-center justify-between px-1 text-xs font-semibold uppercase tracking-wide text-ink-faint"
          >
            <span>In cart ({checked.length})</span>
            <span className={`transition ${cartOpen ? "rotate-180" : ""}`}>▾</span>
          </button>
          {cartOpen && (
            <div className="divide-y divide-hairline overflow-hidden rounded-2xl border border-hairline bg-surface opacity-80">
              {checked.map((it) => (
                <ListRow
                  key={it.id}
                  item={it}
                  pending={pending.has(it.id)}
                  onToggle={(c) => onToggle(it, c)}
                />
              ))}
            </div>
          )}
        </section>
      )}

      {/* also on sale */}
      {list.also_on_sale.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-2 px-1 text-sm font-bold text-ink">Also on sale this week</h2>
          <div className="flex flex-col gap-2">
            {list.also_on_sale.map((d) => (
              <div
                key={d.deal_id}
                className="flex items-center gap-3 rounded-2xl border border-hairline bg-surface p-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">{d.product_name}</p>
                  <p className="text-xs text-brand-dark">
                    🏷️ ${num(d.sale_price).toFixed(2)}
                    {d.savings_pct != null && ` · ${num(d.savings_pct).toFixed(0)}% off`}
                  </p>
                </div>
                <button
                  onClick={() => addExtra(d.product_name, "")}
                  className="h-9 shrink-0 rounded-xl bg-brand-soft px-4 text-sm font-semibold text-brand-dark active:scale-95"
                >
                  + Add
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* sticky done */}
      <div className="fixed inset-x-0 bottom-16 z-30 mx-auto max-w-md border-t border-hairline bg-canvas/95 px-5 py-3 backdrop-blur">
        <button
          onClick={() => setConfirmOpen(true)}
          className="flex h-14 w-full items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white shadow-lg shadow-brand/25 transition active:scale-[.99]"
        >
          ✅ Done shopping
        </button>
      </div>

      {/* confirm dialog */}
      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-6">
          <button
            aria-label="Cancel"
            onClick={() => setConfirmOpen(false)}
            className="absolute inset-0 bg-ink/40"
          />
          <div className="relative w-full max-w-xs rounded-2xl bg-surface p-5 text-center">
            <p className="text-base font-semibold text-ink">
              Add {checked.length} checked item{checked.length === 1 ? "" : "s"} to your pantry?
            </p>
            <p className="mt-1 text-sm text-ink-soft">
              This marks your list complete.
            </p>
            <div className="mt-5 flex gap-3">
              <button
                onClick={() => setConfirmOpen(false)}
                className="h-12 flex-1 rounded-xl border border-hairline bg-surface text-sm font-semibold text-ink"
              >
                Cancel
              </button>
              <button
                onClick={onComplete}
                disabled={completing}
                className="h-12 flex-1 rounded-xl bg-brand text-sm font-semibold text-white disabled:opacity-60"
              >
                {completing ? "Adding…" : "Add to pantry"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function AddExtra({
  open,
  setOpen,
  onAdd,
}: {
  open: boolean;
  setOpen: (v: boolean) => void;
  onAdd: (name: string, qty: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      await onAdd(name, qty);
      setName("");
      setQty("");
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-4 flex h-12 w-full items-center justify-center gap-2 rounded-2xl border border-dashed border-hairline bg-surface text-sm font-medium text-ink-soft active:scale-[.99]"
      >
        + Add item (paper towels, etc.)
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="mt-4 rounded-2xl border border-hairline bg-surface p-4">
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Item name"
        className="h-11 w-full rounded-xl border border-hairline px-3 text-base outline-none focus:border-brand"
      />
      <input
        value={qty}
        onChange={(e) => setQty(e.target.value)}
        placeholder="Quantity (optional)"
        className="mt-2 h-11 w-full rounded-xl border border-hairline px-3 text-base outline-none focus:border-brand"
      />
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="h-11 flex-1 rounded-xl border border-hairline text-sm font-semibold text-ink"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={busy || !name.trim()}
          className="h-11 flex-1 rounded-xl bg-brand text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? "Adding…" : "Add"}
        </button>
      </div>
    </form>
  );
}
