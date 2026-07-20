"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { getCircular, type CircularResponse, type Deal } from "@/lib/dealsApi";

function num(v: string | number | null | undefined): number {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS_SHORT = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"];

function validThrough(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return null;
  return `valid through ${DAYS[d.getDay()]} ${MONTHS_SHORT[d.getMonth()]}/${d.getDate()}`;
}

/** "Cook with this" affordance (P37 C8): lands on Recipes with a 🏷️ deal
 * chip in the Use-up row. */
function cookHref(d: Deal): string {
  const qs = new URLSearchParams({
    pinDeal: String(d.id),
    dealName: d.product_name,
    dealPrice: String(d.sale_price),
  });
  if (d.price_unit) qs.set("dealUnit", d.price_unit);
  return `/recipes?${qs.toString()}`;
}

function DealRow({ d }: { d: Deal }) {
  return (
    <li className="flex items-center gap-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink">{d.product_name}</p>
        {d.brand && <p className="truncate text-xs text-ink-faint">{d.brand}</p>}
      </div>
      <span className="shrink-0 text-sm font-bold text-ink">
        ${num(d.sale_price).toFixed(2)}
        {d.price_unit ? <span className="font-normal text-ink-faint">/{d.price_unit}</span> : null}
      </span>
      {d.savings_pct != null && (
        <span className="shrink-0 rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-bold text-brand-dark">
          {num(d.savings_pct).toFixed(0)}% off
        </span>
      )}
      <Link
        href={cookHref(d)}
        className="shrink-0 rounded-full bg-warn-soft px-2 py-1 text-[11px] font-semibold text-warn active:scale-95"
      >
        🍳 Cook with this
      </Link>
    </li>
  );
}

export default function CircularPage() {
  const params = useParams<{ chain: string }>();
  // "default" is the sentinel for "the user's default store".
  const chain = params.chain && params.chain !== "default" ? params.chain : undefined;

  const [data, setData] = useState<CircularResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const [zoomed, setZoomed] = useState(false);
  const trackRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await getCircular(chain));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load the circular.");
    }
  }, [chain]);

  useEffect(() => {
    load();
    // P41 A: arrived via a flyer-day notification tap (M=0 pantry matches).
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      if (params.get("push") === "1") {
        import("@/lib/eventsApi").then(({ reportEvent }) => reportEvent("push_opened"));
        window.history.replaceState({}, "", window.location.pathname);
      }
    }
  }, [load]);

  // Track the active page from the snap-scroll position (drives dots + strip).
  function onScroll() {
    const el = trackRef.current;
    if (!el || el.clientWidth === 0) return;
    setActive(Math.round(el.scrollLeft / el.clientWidth));
  }

  const header = (
    <header className="mb-4 flex items-center gap-2">
      <Link href="/deals" aria-label="Back" className="text-xl text-ink-soft">
        ‹
      </Link>
      <div className="min-w-0">
        <h1 className="truncate text-xl font-bold text-ink">
          📰 {data?.chain_name ?? "Circular"}
        </h1>
        {data?.valid_to && (
          <p className="text-xs text-ink-soft">
            {data.store_name ? `${data.store_name} · ` : ""}
            {validThrough(data.valid_to)}
          </p>
        )}
      </div>
    </header>
  );

  if (error) {
    return (
      <div className="px-5 pt-8">
        {header}
        <div className="rounded-2xl bg-warn-soft px-4 py-4 text-center text-sm text-warn">
          {error}{" "}
          <button onClick={load} className="font-semibold underline">
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="px-5 pt-8">
        {header}
        <div className="skeleton h-[60vh] w-full rounded-2xl" />
      </div>
    );
  }

  if (data.state === "no_store") {
    return (
      <div className="px-5 pt-8">
        {header}
        <p className="mt-6 text-center text-sm text-ink-soft">
          Save a store first — your circular shows up here.
        </p>
      </div>
    );
  }

  if (data.state === "expired") {
    return (
      <div className="px-5 pt-8">
        {header}
        <div className="mt-6 rounded-2xl border border-hairline bg-surface px-4 py-6 text-center">
          <div className="mb-1 text-2xl">🗞️</div>
          <p className="text-sm text-ink-soft">
            New circular loads{" "}
            {data.refresh_day ? `on ${data.refresh_day}` : "soon"}. Check back
            then.
          </p>
        </div>
      </div>
    );
  }

  if (data.state === "no_images") {
    // Structured-source chains (e.g. Whole Foods) publish deals without flyer
    // pages — the grouped list stands in.
    const byCat = new Map<string, Deal[]>();
    for (const d of data.deals) {
      const k = d.category || "other";
      byCat.set(k, [...(byCat.get(k) ?? []), d]);
    }
    return (
      <div className="px-5 pt-8 pb-8">
        {header}
        <p className="rounded-xl bg-canvas px-3 py-2 text-xs text-ink-soft">
          {data.chain_name ?? "This store"} publishes its weekly deals without
          flyer pages — here&apos;s everything, grouped.
        </p>
        {[...byCat.entries()].map(([cat, list]) => (
          <section key={cat} className="mt-4">
            <h2 className="text-sm font-bold capitalize text-ink">{cat}</h2>
            <ul className="mt-1 divide-y divide-hairline rounded-2xl border border-hairline bg-surface px-4">
              {list.map((d) => (
                <DealRow key={d.id} d={d} />
              ))}
            </ul>
          </section>
        ))}
      </div>
    );
  }

  const pages = data.pages;
  const current = pages[Math.min(active, pages.length - 1)];

  return (
    <div className="pt-8 pb-8">
      <div className="px-5">{header}</div>

      {/* Swipeable full-width pages (scroll-snap). Double-tap a page to zoom;
          pinch works via the browser's native image gestures. */}
      <div
        ref={trackRef}
        onScroll={onScroll}
        className="flex snap-x snap-mandatory overflow-x-auto"
        style={{ touchAction: "pan-x pan-y pinch-zoom" }}
      >
        {pages.map((p) => (
          <div key={p.page_number} className="w-full shrink-0 snap-center px-2">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={p.image_url}
              alt={`Circular page ${p.page_number}`}
              onDoubleClick={() => setZoomed((z) => !z)}
              className={`mx-auto w-full rounded-xl border border-hairline transition-transform ${
                zoomed ? "scale-150 cursor-zoom-out" : "cursor-zoom-in"
              }`}
              loading={p.page_number <= 2 ? "eager" : "lazy"}
            />
          </div>
        ))}
      </div>

      {/* page dots */}
      <div className="mt-3 flex items-center justify-center gap-1.5">
        {pages.map((p, i) => (
          <span
            key={p.page_number}
            className={`h-1.5 rounded-full transition-all ${
              i === active ? "w-4 bg-brand" : "w-1.5 bg-hairline"
            }`}
          />
        ))}
        <span className="ml-2 text-xs text-ink-faint">
          {Math.min(active + 1, pages.length)}/{pages.length}
        </span>
      </div>

      {/* On this page (P37 B3) */}
      <section className="mx-5 mt-4 rounded-2xl border border-hairline bg-surface px-4 py-3">
        <h2 className="text-sm font-bold text-ink">On this page</h2>
        {current && current.deals.length > 0 ? (
          <ul className="divide-y divide-hairline">
            {current.deals.map((d) => (
              <DealRow key={d.id} d={d} />
            ))}
          </ul>
        ) : (
          <p className="py-3 text-sm text-ink-soft">
            No priced deals extracted from this page.
          </p>
        )}
      </section>
    </div>
  );
}
