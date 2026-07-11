"use client";

import type { ShoppingItem } from "@/lib/listTypes";

function money(v: string | number | null | undefined): string | null {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : null;
}

export default function ListRow({
  item,
  pending,
  onToggle,
}: {
  item: ShoppingItem;
  pending: boolean;
  onToggle: (checked: boolean) => void;
}) {
  const price = money(item.price);
  const regular = money(item.regular_price);
  const qty = [item.quantity, item.unit].filter(Boolean).join(" ");
  const recipes = (item.from_recipes ?? []).map((r) => r.title).filter(Boolean);

  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <button
        role="checkbox"
        aria-checked={item.is_checked}
        aria-label={`${item.is_checked ? "Uncheck" : "Check"} ${item.display_name ?? "item"}`}
        disabled={pending}
        onClick={() => onToggle(!item.is_checked)}
        className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border-2 transition active:scale-90 ${
          item.is_checked ? "border-brand bg-brand text-white" : "border-hairline bg-surface"
        }`}
      >
        {item.is_checked && (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M20 6 9 17l-5-5" />
          </svg>
        )}
      </button>

      <div className="min-w-0 flex-1">
        <p
          className={`text-base font-medium ${
            item.is_checked ? "text-ink-faint line-through" : "text-ink"
          }`}
        >
          {item.display_name}
          {qty && <span className="font-normal text-ink-soft"> · {qty}</span>}
        </p>

        {recipes.length > 0 && (
          <p className="mt-0.5 truncate text-xs text-ink-faint">for: {recipes.join(", ")}</p>
        )}
      </div>

      <div className="shrink-0 text-right">
        {price ? (
          <p className="text-base font-semibold text-ink">{price}</p>
        ) : (
          <p className="text-base text-ink-faint">—</p>
        )}
        {item.is_on_sale && (
          <p className="mt-0.5 text-[11px] font-semibold text-brand-dark">
            🏷️ {regular && <span className="font-normal text-ink-faint line-through">{regular}</span>}
            {regular ? " sale" : "on sale"}
          </p>
        )}
      </div>
    </div>
  );
}
