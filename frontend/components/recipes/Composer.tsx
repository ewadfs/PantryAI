"use client";

import { useEffect, useState } from "react";

const EXAMPLES = [
  "grill something…",
  "light + fast…",
  "use the wok…",
  "impress guests…",
  "one pan only…",
  "cozy + comforting…",
];

/**
 * Sticky bottom composer for the Discover tab: a direction input + Generate
 * button, pinned above the bottom tab bar. During generation it swaps to a
 * stepped progress strip. Stays above the on-screen keyboard via visualViewport.
 */
export default function Composer({
  value,
  onChange,
  onGenerate,
  generating,
  stepText,
  lastDirection,
}: {
  value: string;
  onChange: (v: string) => void;
  onGenerate: () => void;
  generating: boolean;
  stepText: string;
  lastDirection: string;
}) {
  // Rotate the placeholder through examples until the user has generated once.
  const [ex, setEx] = useState(0);
  useEffect(() => {
    if (lastDirection) return;
    const t = setInterval(() => setEx((i) => (i + 1) % EXAMPLES.length), 2600);
    return () => clearInterval(t);
  }, [lastDirection]);

  // Keep the composer above the on-screen keyboard.
  const [kbInset, setKbInset] = useState(0);
  useEffect(() => {
    const vv = typeof window !== "undefined" ? window.visualViewport : null;
    if (!vv) return;
    const onResize = () => {
      const overlap = window.innerHeight - (vv.height + vv.offsetTop);
      setKbInset(Math.max(0, Math.round(overlap)));
    };
    vv.addEventListener("resize", onResize);
    vv.addEventListener("scroll", onResize);
    onResize();
    return () => {
      vv.removeEventListener("resize", onResize);
      vv.removeEventListener("scroll", onResize);
    };
  }, []);

  const placeholder = lastDirection
    ? `again: “${lastDirection}”?`
    : EXAMPLES[ex];

  return (
    <div
      className={`fixed inset-x-0 z-40 mx-auto max-w-md border-t border-hairline bg-canvas/95 px-4 py-3 backdrop-blur ${
        kbInset > 0 ? "" : "bottom-16"
      }`}
      style={kbInset > 0 ? { bottom: kbInset } : undefined}
    >
      {generating ? (
        <div className="flex h-12 items-center justify-center gap-2 rounded-2xl bg-brand-soft px-4 text-sm font-medium text-brand-dark">
          <span className="inline-block h-2.5 w-2.5 animate-ping rounded-full bg-brand" />
          {stepText}
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!generating) onGenerate();
          }}
          className="flex items-center gap-2"
        >
          <input
            value={value}
            onChange={(e) => onChange(e.target.value.slice(0, 200))}
            placeholder={placeholder}
            enterKeyHint="go"
            className="h-12 flex-1 rounded-2xl border border-hairline bg-surface px-4 text-base text-ink outline-none focus:border-brand"
          />
          <button
            type="submit"
            disabled={generating}
            className="flex h-12 shrink-0 items-center gap-1 rounded-2xl bg-brand px-5 text-sm font-semibold text-white shadow-lg shadow-brand/25 transition active:scale-[.98] disabled:opacity-60"
          >
            ✨ Generate
          </button>
        </form>
      )}
    </div>
  );
}
