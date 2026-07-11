"use client";

import { useEffect } from "react";
import type { UserStore } from "@/lib/listTypes";

export default function StoreSheet({
  stores,
  currentId,
  switching,
  onSelect,
  onClose,
}: {
  stores: UserStore[];
  currentId: number | null;
  switching: boolean;
  onSelect: (storeLocationId: number) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end">
      <button aria-label="Close" onClick={onClose} className="absolute inset-0 bg-ink/40" />
      <div className="relative mx-auto w-full max-w-md rounded-t-3xl bg-surface pb-8">
        <div className="px-5 pt-5">
          <h2 className="text-lg font-bold text-ink">Where are you shopping this week?</h2>
          <p className="mt-1 text-sm text-ink-soft">
            Deals, recipes, and your list all follow this store.
          </p>
        </div>

        <div className="mt-4 flex flex-col">
          {stores.map((s) => {
            const active = s.store.id === currentId;
            return (
              <button
                key={s.store.id}
                disabled={switching}
                onClick={() => onSelect(s.store.id)}
                className="flex items-center gap-3 border-t border-hairline px-5 py-4 text-left active:bg-canvas disabled:opacity-60"
              >
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 ${
                    active ? "border-brand bg-brand text-white" : "border-hairline"
                  }`}
                >
                  {active && <span className="text-xs">✓</span>}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-base font-medium text-ink">
                    {s.store.store_name}
                  </span>
                  <span className="block truncate text-xs text-ink-faint">
                    {s.store.chain_name}
                  </span>
                </span>
                {active && <span className="shrink-0 text-xs font-semibold text-brand-dark">Current</span>}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
