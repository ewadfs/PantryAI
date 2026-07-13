"use client";

/** The anchored-store chip (Prompt 15 → shared in Prompt 37): one component,
 * two homes — the Recipes setup panel and the Home deals card. Tapping it
 * opens the same StoreSheet bottom sheet wherever it lives. */
export default function StoreChip({
  storeName,
  onOpen,
}: {
  storeName: string;
  onOpen: () => void;
}) {
  return (
    <button
      onClick={onOpen}
      className="flex shrink-0 items-center gap-1 rounded-full border border-hairline bg-canvas px-3 py-1.5 text-sm font-medium text-ink active:scale-[.98]"
    >
      📍 <span className="max-w-[10rem] truncate">{storeName}</span>
      <span className="text-ink-faint">▾</span>
    </button>
  );
}
