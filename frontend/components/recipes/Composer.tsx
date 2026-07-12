"use client";

import { useEffect, useState } from "react";
import type { Difficulty } from "@/lib/recipeApi";

const TIERS: { key: Difficulty; label: string }[] = [
  { key: "easy", label: "Easy" },
  { key: "medium", label: "Medium" },
  { key: "hard", label: "Hard" },
];

// Tier colors: easy green, medium amber, hard red.
const TIER_STYLE: Record<Difficulty, { on: string; off: string }> = {
  easy: { on: "bg-brand text-white border-brand", off: "bg-surface text-brand-dark border-hairline" },
  medium: { on: "bg-warn text-white border-warn", off: "bg-surface text-warn border-hairline" },
  hard: { on: "bg-red-600 text-white border-red-600", off: "bg-surface text-red-600 border-hairline" },
};

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
  difficulties,
  onToggleDifficulty,
}: {
  value: string;
  onChange: (v: string) => void;
  onGenerate: () => void;
  generating: boolean;
  stepText: string;
  lastDirection: string;
  difficulties: Difficulty[];
  onToggleDifficulty: (d: Difficulty) => void;
}) {
  // Transient shake when the user tries to turn off the last active tier.
  const [shake, setShake] = useState<Difficulty | null>(null);
  function toggle(d: Difficulty) {
    const isOn = difficulties.includes(d);
    if (isOn && difficulties.length <= 1) {
      setShake(d);
      window.setTimeout(() => setShake((s) => (s === d ? null : s)), 400);
      return; // keep at least one tier on
    }
    onToggleDifficulty(d);
  }
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
        <>
          <div className="mb-2 flex items-center gap-2">
            {TIERS.map(({ key, label }) => {
              const on = difficulties.includes(key);
              const st = TIER_STYLE[key];
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => toggle(key)}
                  aria-pressed={on}
                  className={`h-8 flex-1 rounded-full border text-xs font-semibold transition active:scale-95 ${
                    on ? st.on : st.off
                  } ${shake === key ? "animate-shake" : ""}`}
                >
                  {label}
                </button>
              );
            })}
          </div>
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
        </>
      )}
    </div>
  );
}
