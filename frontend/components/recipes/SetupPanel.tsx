"use client";

import { forwardRef, useEffect, useState, type RefObject } from "react";
import type { Difficulty } from "@/lib/recipeApi";
import type { PantryItem } from "@/lib/types";
import StoreChip from "@/components/stores/StoreChip";
import UseUpRow, { type Pin } from "./UseUpRow";

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
 * Top setup panel for the Discover tab (Prompt 29): a single grouped card
 * holding all generation configuration — store chip, "use up" pins, difficulty
 * chips, direction input, and the primary Generate button. Scrolls with content
 * (not sticky). During generation the button swaps to the stepped progress strip.
 */
const SetupPanel = forwardRef<HTMLDivElement, {
  storeName: string | null;
  onOpenStore: () => void;
  pins: Pin[];
  pantryItems: PantryItem[];
  onAddPin: (p: Pin) => void;
  onRemovePin: (id: number, kind?: "pantry" | "deal") => void;
  pantryMode: boolean;
  onTogglePantryMode: () => void;
  difficulties: Difficulty[];
  onToggleDifficulty: (d: Difficulty) => void;
  direction: string;
  onChangeDirection: (v: string) => void;
  lastDirection: string;
  directionRef: RefObject<HTMLInputElement | null>;
  onGenerate: () => void;
  generating: boolean;
  stepText: string;
}>(function SetupPanel(props, ref) {
  const {
    storeName, onOpenStore, pins, pantryItems, onAddPin, onRemovePin,
    pantryMode, onTogglePantryMode,
    difficulties, onToggleDifficulty, direction, onChangeDirection,
    lastDirection, directionRef, onGenerate, generating, stepText,
  } = props;

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

  const placeholder = lastDirection ? `again: “${lastDirection}”?` : EXAMPLES[ex];

  return (
    <div
      ref={ref}
      className="rounded-2xl border border-hairline bg-surface p-4 shadow-sm"
    >
      {/* store chip (shared with the Home deals card, P37 A) */}
      {storeName && (
        <div className="mb-3 flex justify-end">
          <StoreChip storeName={storeName} onOpen={onOpenStore} />
        </div>
      )}

      {/* use-up pins */}
      <UseUpRow
        pins={pins}
        pantryItems={pantryItems}
        onAdd={onAddPin}
        onRemove={onRemovePin}
      />

      {/* pantry mode (Prompt 35): minimize buying — market slots off, at most
          one minor purchase per recipe. Persisted; warm cache honors it. */}
      <button
        type="button"
        role="switch"
        aria-checked={pantryMode}
        onClick={onTogglePantryMode}
        className={`mb-3 flex w-full items-center justify-between rounded-xl border px-3 py-2 text-left transition ${
          pantryMode
            ? "border-brand bg-brand-soft"
            : "border-hairline bg-canvas"
        }`}
      >
        <span
          className={`text-xs font-semibold ${
            pantryMode ? "text-brand-dark" : "text-ink-soft"
          }`}
        >
          🏠 Cook from what I have — minimize buying
        </span>
        <span
          className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
            pantryMode ? "bg-brand" : "bg-hairline"
          }`}
        >
          <span
            className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
              pantryMode ? "translate-x-[18px]" : "translate-x-0.5"
            }`}
          />
        </span>
      </button>

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
              ref={directionRef}
              value={direction}
              onChange={(e) => onChangeDirection(e.target.value.slice(0, 200))}
              placeholder={placeholder}
              enterKeyHint="go"
              className="h-12 flex-1 rounded-2xl border border-hairline bg-canvas px-4 text-base text-ink outline-none focus:border-brand"
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
});

export default SetupPanel;
