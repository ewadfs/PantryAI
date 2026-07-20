"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  discoverStores,
  replaceMyStores,
  type DiscoveredStore,
} from "@/lib/storesApi";

/**
 * ZIP-first onboarding (P40 B). The ONLY required setup is picking a store:
 * ZIP → discovered stores → tap one → recipe generation fires immediately
 * (the Recipes page shows the store's real deals as the loading state).
 * Everything else — scan, taste, household — arrives later as optional
 * upgrade cards in the recipe feed.
 */
export default function WelcomePage() {
  const router = useRouter();
  // P41 B: arrived via a shared recipe's CTA — count the conversion once
  // the account exists (we're behind auth here, so signup already happened).
  useEffect(() => {
    const ref = new URLSearchParams(window.location.search).get("ref");
    if (ref) {
      import("@/lib/eventsApi").then(({ reportEvent }) =>
        reportEvent("share_converted", { slug: ref }),
      );
      window.history.replaceState({}, "", "/welcome");
    }
  }, []);
  const [zip, setZip] = useState("");
  const [searching, setSearching] = useState(false);
  const [stores, setStores] = useState<DiscoveredStore[] | null>(null);
  const [pickingId, setPickingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSearch(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSearching(true);
    try {
      const res = await discoverStores(zip.trim());
      setStores(res.stores);
      if (res.stores.length === 0) {
        setError(
          "No supported stores near that ZIP yet. Try a neighboring ZIP — we're adding stores every week.",
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't look up stores.");
    } finally {
      setSearching(false);
    }
  }

  async function onPick(store: DiscoveredStore) {
    if (pickingId !== null) return;
    setPickingId(store.id);
    setError(null);
    try {
      await replaceMyStores([store.id], store.id);
      // Fire generation NOW — the Recipes page renders this store's top deals
      // as the loading state, so real prices are on screen within seconds.
      router.replace(
        `/recipes?generate=1&welcome=1&store=${encodeURIComponent(
          store.store_name ?? store.chain_name,
        )}`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that store.");
      setPickingId(null);
    }
  }

  return (
    <div className="mx-auto max-w-md px-5 pt-10">
      <div className="mb-8 text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand text-2xl text-white">
          🛒
        </div>
        <h1 className="text-2xl font-bold text-ink">Where do you shop?</h1>
        <p className="mt-2 text-sm text-ink-soft">
          Pick your store and we&apos;ll build tonight&apos;s dinners from its{" "}
          <span className="font-semibold text-ink">real weekly deals</span>. That&apos;s
          the whole setup.
        </p>
      </div>

      <form onSubmit={onSearch} className="flex gap-2">
        <input
          type="text"
          inputMode="numeric"
          autoComplete="postal-code"
          pattern="\d{5}"
          maxLength={5}
          required
          value={zip}
          onChange={(e) => setZip(e.target.value.replace(/\D/g, ""))}
          placeholder="ZIP code"
          className="h-12 min-w-0 flex-1 rounded-2xl border border-hairline bg-surface px-4 text-base text-ink outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
        />
        <button
          type="submit"
          disabled={searching || zip.length !== 5}
          className="h-12 shrink-0 rounded-2xl bg-brand px-5 text-sm font-semibold text-white transition active:scale-[.99] disabled:opacity-60"
        >
          {searching ? "Searching…" : "Find stores"}
        </button>
      </form>

      {error && (
        <p className="mt-4 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">
          {error}
        </p>
      )}

      {searching && (
        <div className="mt-5 flex flex-col gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="rounded-2xl border border-hairline bg-surface p-4">
              <div className="skeleton h-5 w-2/3 rounded" />
              <div className="skeleton mt-2 h-4 w-1/2 rounded" />
            </div>
          ))}
        </div>
      )}

      {stores && stores.length > 0 && !searching && (
        <ul className="mt-5 flex flex-col gap-3">
          {stores.map((s) => (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onPick(s)}
                disabled={pickingId !== null}
                className={`w-full rounded-2xl border p-4 text-left transition active:scale-[.99] disabled:opacity-60 ${
                  pickingId === s.id
                    ? "border-brand bg-brand-soft"
                    : "border-hairline bg-surface"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-base font-semibold text-ink">
                    {s.store_name ?? s.chain_name}
                  </span>
                  {s.distance_miles != null && (
                    <span className="shrink-0 text-xs text-ink-faint">
                      {Number(s.distance_miles).toFixed(1)} mi
                    </span>
                  )}
                </div>
                <p className="mt-0.5 truncate text-sm text-ink-soft">
                  {[s.address, s.city].filter(Boolean).join(", ") || s.chain_name}
                </p>
                <p className="mt-1.5 text-xs font-medium">
                  {pickingId === s.id ? (
                    <span className="text-brand-dark">
                      Grabbing this week&apos;s deals…
                    </span>
                  ) : s.has_deals_source ? (
                    <span className="text-brand-dark">
                      🏷️ Weekly deals available — tap to start
                    </span>
                  ) : (
                    <span className="text-ink-faint">
                      Deals coming soon — recipes still work
                    </span>
                  )}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="h-10" />
    </div>
  );
}
