"use client";

import { useMemo, useState } from "react";
import type { PantryItem } from "@/lib/types";

export type Pin = { id: number; name: string };
const MAX_PINS = 3;

export default function UseUpRow({
  pins,
  pantryItems,
  onAdd,
  onRemove,
}: {
  pins: Pin[];
  pantryItems: PantryItem[];
  onAdd: (pin: Pin) => void;
  onRemove: (id: number) => void;
}) {
  const [picking, setPicking] = useState(false);
  const atMax = pins.length >= MAX_PINS;

  return (
    <div className="mb-4 rounded-2xl border border-hairline bg-surface p-4">
      <p className="text-sm font-semibold text-ink">Use up something specific?</p>
      <p className="mt-0.5 text-xs text-ink-soft">
        Pin up to {MAX_PINS} — every recipe will feature them.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {pins.map((p) => (
          <span
            key={p.id}
            className="flex items-center gap-1.5 rounded-full bg-brand-soft px-3 py-1.5 text-sm font-medium text-brand-dark"
          >
            {p.name}
            <button aria-label={`Unpin ${p.name}`} onClick={() => onRemove(p.id)} className="text-brand-dark/70">
              ✕
            </button>
          </span>
        ))}
        {!atMax && (
          <button
            onClick={() => setPicking(true)}
            className="rounded-full border border-dashed border-hairline px-3 py-1.5 text-sm font-medium text-ink-soft"
          >
            + pin an item
          </button>
        )}
      </div>

      {picking && (
        <PantryPicker
          items={pantryItems.filter((i) => !pins.some((p) => p.id === i.id))}
          onPick={(item) => {
            onAdd({ id: item.id, name: item.name ?? "item" });
            setPicking(false);
          }}
          onClose={() => setPicking(false)}
        />
      )}
    </div>
  );
}

function PantryPicker({
  items,
  onPick,
  onClose,
}: {
  items: PantryItem[];
  onPick: (item: PantryItem) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    const list = s ? items.filter((i) => (i.name ?? "").toLowerCase().includes(s)) : items;
    return list.slice(0, 40);
  }, [q, items]);

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end">
      <button aria-label="Close" onClick={onClose} className="absolute inset-0 bg-ink/40" />
      <div className="relative mx-auto max-h-[80vh] w-full max-w-md overflow-hidden rounded-t-3xl bg-surface">
        <div className="border-b border-hairline p-4">
          <h2 className="mb-3 text-base font-bold text-ink">Pin a pantry item</h2>
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search your pantry…"
            className="h-11 w-full rounded-xl border border-hairline px-3 text-base outline-none focus:border-brand"
          />
        </div>
        <div className="max-h-[55vh] overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="p-6 text-center text-sm text-ink-soft">No matching items.</p>
          ) : (
            filtered.map((it) => (
              <button
                key={it.id}
                onClick={() => onPick(it)}
                className="flex w-full items-center justify-between border-t border-hairline px-4 py-3 text-left first:border-t-0 active:bg-canvas"
              >
                <span className="min-w-0 flex-1 truncate text-base text-ink">{it.name}</span>
                {it.use_soon && (
                  <span className="ml-2 shrink-0 rounded-full bg-warn-soft px-2 py-0.5 text-[11px] font-semibold text-warn">
                    use soon
                  </span>
                )}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
