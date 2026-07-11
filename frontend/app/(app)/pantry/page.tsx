"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  addPantryItem,
  deletePantryItem,
  listPantry,
  updatePantryItem,
} from "@/lib/pantryApi";
import { categoryLabel } from "@/lib/categories";
import type { PantryItem, PantryListResponse } from "@/lib/types";

export default function PantryPage() {
  const [data, setData] = useState<PantryListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await listPantry());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load your pantry.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const items = useMemo(
    () => (data ? data.categories.flatMap((g) => g.items) : []),
    [data],
  );
  const useSoon = items.filter((i) => i.use_soon);
  const staples = items.filter((i) => i.is_staple);
  const regularGroups = useMemo(() => {
    const map = new Map<string, PantryItem[]>();
    for (const it of items) {
      if (it.is_staple || it.use_soon) continue;
      const cat = it.category || "other";
      (map.get(cat) ?? map.set(cat, []).get(cat)!).push(it);
    }
    return [...map.entries()];
  }, [items]);

  async function onDelete(id: number) {
    setData((d) =>
      d
        ? {
            ...d,
            count: d.count - 1,
            categories: d.categories.map((g) => ({
              ...g,
              items: g.items.filter((i) => i.id !== id),
            })),
          }
        : d,
    );
    try {
      await deletePantryItem(id);
    } catch {
      load();
    }
  }

  async function onQty(id: number, quantity_estimate: string) {
    setData((d) =>
      d
        ? {
            ...d,
            categories: d.categories.map((g) => ({
              ...g,
              items: g.items.map((i) =>
                i.id === id ? { ...i, quantity_estimate } : i,
              ),
            })),
          }
        : d,
    );
    try {
      await updatePantryItem(id, { quantity_estimate });
    } catch {
      load();
    }
  }

  return (
    <div className="px-5 pt-8">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-ink">Your pantry</h1>
          <p className="mt-1 text-sm text-ink-soft">
            {data ? `${data.count} active item${data.count === 1 ? "" : "s"}` : " "}
          </p>
        </div>
        <Link
          href="/scan"
          className="flex h-11 shrink-0 items-center gap-1.5 rounded-2xl bg-brand px-4 text-sm font-semibold text-white active:scale-[.99]"
        >
          📸 Rescan
        </Link>
      </header>

      <button
        onClick={() => setAdding((v) => !v)}
        className="mt-4 flex h-12 w-full items-center justify-center gap-2 rounded-2xl border border-hairline bg-surface text-sm font-medium text-ink active:scale-[.99]"
      >
        + Add an item
      </button>
      {adding && (
        <AddForm
          onDone={() => {
            setAdding(false);
            load();
          }}
        />
      )}

      {loading && <p className="mt-8 text-center text-sm text-ink-soft">Loading…</p>}
      {error && (
        <p className="mt-6 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">
          {error}
        </p>
      )}

      {!loading && !error && data && data.count === 0 && (
        <div className="mt-8 rounded-2xl border border-dashed border-hairline bg-surface p-8 text-center">
          <p className="text-sm text-ink-soft">
            Your pantry is empty. Scan your kitchen to get started.
          </p>
          <Link
            href="/scan"
            className="mt-4 inline-flex h-12 items-center justify-center rounded-2xl bg-brand px-6 text-sm font-semibold text-white"
          >
            Scan your kitchen
          </Link>
        </div>
      )}

      {useSoon.length > 0 && (
        <section className="mt-6">
          <h2 className="mb-2 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-wide text-warn">
            <span>Use soon</span>
          </h2>
          <div className="overflow-hidden rounded-2xl border border-warn/30 bg-warn-soft/40">
            {useSoon.map((it, i) => (
              <Row key={it.id} item={it} first={i === 0} onDelete={onDelete} onQty={onQty} warn />
            ))}
          </div>
        </section>
      )}

      {regularGroups.map(([cat, rows]) => (
        <section key={cat} className="mt-6">
          <h2 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
            {categoryLabel(cat)}
          </h2>
          <div className="overflow-hidden rounded-2xl border border-hairline bg-surface">
            {rows.map((it, i) => (
              <Row key={it.id} item={it} first={i === 0} onDelete={onDelete} onQty={onQty} />
            ))}
          </div>
        </section>
      ))}

      {staples.length > 0 && <StaplesSection staples={staples} onDelete={onDelete} onQty={onQty} />}
    </div>
  );
}

function StaplesSection({
  staples,
  onDelete,
  onQty,
}: {
  staples: PantryItem[];
  onDelete: (id: number) => void;
  onQty: (id: number, q: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="mt-6">
      <button
        onClick={() => setOpen((v) => !v)}
        className="mb-2 flex w-full items-center justify-between px-1 text-xs font-semibold uppercase tracking-wide text-ink-faint"
      >
        <span>Staples ({staples.length})</span>
        <span className={`transition ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open && (
        <div className="overflow-hidden rounded-2xl border border-hairline bg-surface">
          {staples.map((it, i) => (
            <Row key={it.id} item={it} first={i === 0} onDelete={onDelete} onQty={onQty} />
          ))}
        </div>
      )}
    </section>
  );
}

function Row({
  item,
  first,
  onDelete,
  onQty,
  warn = false,
}: {
  item: PantryItem;
  first: boolean;
  onDelete: (id: number) => void;
  onQty: (id: number, q: string) => void;
  warn?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  return (
    <div
      className={`flex items-center gap-3 px-4 py-3 ${
        first ? "" : warn ? "border-t border-warn/20" : "border-t border-hairline"
      }`}
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-base font-medium text-ink">{item.name}</p>
        {editing ? (
          <input
            autoFocus
            defaultValue={item.quantity_estimate ?? ""}
            placeholder="quantity"
            onBlur={(e) => {
              onQty(item.id, e.target.value.trim());
              setEditing(false);
            }}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            className="mt-1 w-32 rounded-lg border border-brand/40 px-2 py-1 text-sm outline-none"
          />
        ) : (
          <button onClick={() => setEditing(true)} className="mt-0.5 text-sm text-ink-soft">
            {item.quantity_estimate
              ? `${item.quantity_estimate}${item.unit ? " " + item.unit : ""}`
              : "add amount"}
          </button>
        )}
      </div>
      <button
        aria-label={`Remove ${item.name ?? "item"}`}
        onClick={() => onDelete(item.id)}
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-ink-faint transition active:scale-90"
      >
        <span className="text-lg leading-none">✕</span>
      </button>
    </div>
  );
}

function AddForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await addPantryItem({ name: name.trim(), quantity_estimate: qty.trim() || null });
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not add item.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-3 rounded-2xl border border-hairline bg-surface p-4">
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
      {err && <p className="mt-2 text-sm text-warn">{err}</p>}
      <button
        type="submit"
        disabled={busy || !name.trim()}
        className="mt-3 flex h-11 w-full items-center justify-center rounded-xl bg-brand text-sm font-semibold text-white disabled:opacity-50"
      >
        {busy ? "Adding…" : "Add to pantry"}
      </button>
    </form>
  );
}
