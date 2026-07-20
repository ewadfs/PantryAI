"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { updateMe, type UserProfile } from "@/lib/userApi";

/**
 * Progressive upgrade cards (P40 B5). Shown AFTER the first recipe batch —
 * never as gates before it. Each card covers one missing piece of context,
 * is dismissible, retires forever once completed, and tells the user exactly
 * how the NEXT batch improves. Existing users with complete profiles see
 * nothing.
 */

const DISMISS_PREFIX = "pantryai:upgrade:dismissed:";

function isDismissed(key: string): boolean {
  if (typeof window === "undefined") return true;
  try {
    return localStorage.getItem(DISMISS_PREFIX + key) === "1";
  } catch {
    return false;
  }
}

function dismiss(key: string) {
  try {
    localStorage.setItem(DISMISS_PREFIX + key, "1");
  } catch {
    /* best-effort */
  }
}

// Profile defaults (backend User model): a user still on ALL of these has
// never told us about their household.
const DEFAULT_HOUSEHOLD = 4;
const DEFAULT_PROTEIN = 100;

type Props = {
  me: UserProfile | null;
  pantryCount: number;
  onProfileSaved: (fresh: UserProfile) => void;
  onGenerate: () => void;
};

export default function UpgradeCards({
  me,
  pantryCount,
  onProfileSaved,
  onGenerate,
}: Props) {
  // Re-render trigger for dismissals (localStorage isn't reactive).
  const [, setTick] = useState(0);
  const bump = () => setTick((t) => t + 1);
  // Dismissal state lives in localStorage — render nothing until mounted so
  // SSR/hydration never disagree about which card shows.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!me || !mounted) return null;

  const cards: React.ReactNode[] = [];

  if (pantryCount === 0 && !isDismissed("scan")) {
    cards.push(
      <ScanCard key="scan" onDismiss={() => (dismiss("scan"), bump())} />,
    );
  }

  if (!me.taste_notes?.trim() && !isDismissed("taste")) {
    cards.push(
      <TasteCard
        key="taste"
        onDismiss={() => (dismiss("taste"), bump())}
        onSaved={onProfileSaved}
        onGenerate={onGenerate}
      />,
    );
  }

  if (
    me.household_size === DEFAULT_HOUSEHOLD &&
    me.protein_target === DEFAULT_PROTEIN &&
    !isDismissed("household")
  ) {
    cards.push(
      <HouseholdCard
        key="household"
        onDismiss={() => (dismiss("household"), bump())}
        onSaved={onProfileSaved}
        onGenerate={onGenerate}
      />,
    );
  }

  if (cards.length === 0) return null;

  // One at a time: the first unfinished card only — progressive, not a wall.
  return <div className="mt-4">{cards[0]}</div>;
}

function CardShell({
  emoji,
  title,
  subtitle,
  onDismiss,
  children,
}: {
  emoji: string;
  title: string;
  subtitle: string;
  onDismiss: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="relative rounded-2xl border border-brand/25 bg-brand-soft/60 p-4">
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss"
        className="absolute right-3 top-3 flex h-7 w-7 items-center justify-center rounded-full text-ink-faint transition hover:bg-black/5 hover:text-ink"
      >
        ✕
      </button>
      <div className="flex items-start gap-3 pr-8">
        <span className="text-2xl" aria-hidden>
          {emoji}
        </span>
        <div>
          <h3 className="text-sm font-bold text-ink">{title}</h3>
          <p className="mt-0.5 text-xs text-ink-soft">{subtitle}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

function DoneState({ message, onGenerate }: { message: string; onGenerate: () => void }) {
  return (
    <div className="mt-3">
      <p className="text-sm font-medium text-brand-dark">✓ {message}</p>
      <button
        type="button"
        onClick={onGenerate}
        className="mt-3 flex h-10 w-full items-center justify-center rounded-xl bg-brand text-sm font-semibold text-white active:scale-[.99]"
      >
        ✨ Generate a fresh batch
      </button>
    </div>
  );
}

/* ---------- a. Scan your kitchen ---------- */
function ScanCard({ onDismiss }: { onDismiss: () => void }) {
  return (
    <CardShell
      emoji="📸"
      title="Cook from what you already own"
      subtitle="Scan your kitchen and the next batch is priced against YOUR shelves — ingredients you own cost $0."
      onDismiss={onDismiss}
    >
      <Link
        href="/scan"
        className="mt-3 flex h-10 w-full items-center justify-center rounded-xl bg-brand text-sm font-semibold text-white active:scale-[.99]"
      >
        Scan my kitchen (30 seconds)
      </Link>
    </CardShell>
  );
}

/* ---------- b. 30-second taste ---------- */
const HEAT = ["Mild", "Medium heat", "Bring the heat"] as const;
const ADVENTURE = ["Keep it classic", "Mix it up", "Surprise me"] as const;
const TIME = [20, 30, 45] as const;

function TasteCard({
  onDismiss,
  onSaved,
  onGenerate,
}: {
  onDismiss: () => void;
  onSaved: (fresh: UserProfile) => void;
  onGenerate: () => void;
}) {
  const [heat, setHeat] = useState<string | null>(null);
  const [adventure, setAdventure] = useState<string | null>(null);
  const [time, setTime] = useState<number | null>(null);
  const [free, setFree] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSave() {
    setSaving(true);
    setError(null);
    const parts = [
      heat && `Spice: ${heat.toLowerCase()}.`,
      adventure && `Style: ${adventure.toLowerCase()}.`,
      free.trim(),
    ].filter(Boolean);
    try {
      const fresh = await updateMe({
        taste_notes: parts.join(" "),
        ...(time ? { max_prep_time: time } : {}),
      });
      onSaved(fresh);
      setDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your taste.");
    } finally {
      setSaving(false);
    }
  }

  const chip = (active: boolean) =>
    `rounded-full border px-3 py-1.5 text-xs font-semibold transition active:scale-95 ${
      active
        ? "border-brand bg-brand text-white"
        : "border-hairline bg-surface text-ink-soft"
    }`;

  return (
    <CardShell
      emoji="🌶️"
      title="30-second taste check"
      subtitle="Three taps and the chef stops guessing what you like."
      onDismiss={onDismiss}
    >
      {done ? (
        <DoneState
          message="Taste saved — your next batch is seasoned for you, not the average household."
          onGenerate={onGenerate}
        />
      ) : (
        <div className="mt-3 flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            {HEAT.map((h) => (
              <button key={h} type="button" onClick={() => setHeat(h)} className={chip(heat === h)}>
                {h}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            {ADVENTURE.map((a) => (
              <button
                key={a}
                type="button"
                onClick={() => setAdventure(a)}
                className={chip(adventure === a)}
              >
                {a}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-ink-soft">Weeknight cooking time:</span>
            {TIME.map((t) => (
              <button key={t} type="button" onClick={() => setTime(t)} className={chip(time === t)}>
                {t} min
              </button>
            ))}
          </div>
          <input
            type="text"
            value={free}
            onChange={(e) => setFree(e.target.value)}
            placeholder="Anything else? e.g. “kids hate mushrooms”"
            maxLength={200}
            className="h-10 rounded-xl border border-hairline bg-surface px-3 text-sm text-ink outline-none focus:border-brand"
          />
          {error && <p className="text-xs text-warn">{error}</p>}
          <button
            type="button"
            onClick={onSave}
            disabled={saving || (!heat && !adventure && !time && !free.trim())}
            className="flex h-10 w-full items-center justify-center rounded-xl bg-brand text-sm font-semibold text-white active:scale-[.99] disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save my taste"}
          </button>
        </div>
      )}
    </CardShell>
  );
}

/* ---------- c. Household size + protein goal ---------- */
function HouseholdCard({
  onDismiss,
  onSaved,
  onGenerate,
}: {
  onDismiss: () => void;
  onSaved: (fresh: UserProfile) => void;
  onGenerate: () => void;
}) {
  const [size, setSize] = useState(DEFAULT_HOUSEHOLD);
  const [protein, setProtein] = useState(DEFAULT_PROTEIN);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSave() {
    setSaving(true);
    setError(null);
    try {
      const fresh = await updateMe({ household_size: size, protein_target: protein });
      onSaved(fresh);
      setDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <CardShell
      emoji="👨‍👩‍👧"
      title="Who's at the table?"
      subtitle="Portions and protein are sized for a default household of 4 until you say otherwise."
      onDismiss={onDismiss}
    >
      {done ? (
        <DoneState
          message={`Portions now sized for ${size} with ${protein}g protein per serving in mind.`}
          onGenerate={onGenerate}
        />
      ) : (
        <div className="mt-3 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-ink">Household size</span>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setSize((s) => Math.max(1, s - 1))}
                className="flex h-8 w-8 items-center justify-center rounded-full border border-hairline bg-surface text-lg font-semibold text-ink active:scale-95"
                aria-label="Fewer people"
              >
                −
              </button>
              <span className="w-6 text-center text-base font-bold text-ink">{size}</span>
              <button
                type="button"
                onClick={() => setSize((s) => Math.min(12, s + 1))}
                className="flex h-8 w-8 items-center justify-center rounded-full border border-hairline bg-surface text-lg font-semibold text-ink active:scale-95"
                aria-label="More people"
              >
                +
              </button>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-ink">Protein goal (g/day)</span>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setProtein((p) => Math.max(40, p - 10))}
                className="flex h-8 w-8 items-center justify-center rounded-full border border-hairline bg-surface text-lg font-semibold text-ink active:scale-95"
                aria-label="Lower protein goal"
              >
                −
              </button>
              <span className="w-10 text-center text-base font-bold text-ink">{protein}</span>
              <button
                type="button"
                onClick={() => setProtein((p) => Math.min(250, p + 10))}
                className="flex h-8 w-8 items-center justify-center rounded-full border border-hairline bg-surface text-lg font-semibold text-ink active:scale-95"
                aria-label="Higher protein goal"
              >
                +
              </button>
            </div>
          </div>
          {error && <p className="text-xs text-warn">{error}</p>}
          <button
            type="button"
            onClick={onSave}
            disabled={saving}
            className="flex h-10 w-full items-center justify-center rounded-xl bg-brand text-sm font-semibold text-white active:scale-[.99] disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      )}
    </CardShell>
  );
}
