"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase";
import { useToast } from "@/components/ui/Toast";
import { getMe, updateMe, type UserProfile, type UserUpdate } from "@/lib/userApi";
import {
  discoverStores,
  getAllStores,
  getMyStores,
  replaceMyStores,
} from "@/lib/storesApi";
import type { StoreLocation } from "@/lib/listTypes";

const GOALS: [string, string][] = [
  ["eat_healthy", "Eat healthy"],
  ["save_money", "Save money"],
  ["lose_weight", "Lose weight"],
  ["build_muscle", "Build muscle"],
  ["eat_variety", "More variety"],
];
const DIETS = ["omnivore", "vegetarian", "vegan", "pescatarian", "keto", "paleo"];
const SKILLS = ["beginner", "intermediate", "advanced"];
const ALLERGY_PRESETS = ["nuts", "dairy", "gluten", "shellfish", "soy", "egg"];
const CUISINE_PRESETS = [
  "italian", "mexican", "chinese", "indian", "thai", "japanese",
  "mediterranean", "american", "french", "korean",
];
const MAX_STORES = 5;
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export default function SettingsPage() {
  const router = useRouter();
  const toast = useToast();

  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState<UserProfile | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);

  const [allStores, setAllStores] = useState<StoreLocation[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [defaultId, setDefaultId] = useState<number | null>(null);
  const [savingStores, setSavingStores] = useState(false);
  const [zip, setZip] = useState("");
  const [discovering, setDiscovering] = useState(false);
  // Deals status per store id (from discovery) → "deals coming soon" badge.
  const [dealsStatusById, setDealsStatusById] = useState<Record<number, string>>({});

  async function onDiscover() {
    const z = zip.trim();
    if (!/^\d{5}$/.test(z)) {
      toast.error("Enter a 5-digit ZIP.");
      return;
    }
    setDiscovering(true);
    try {
      const res = await discoverStores(z);
      setAllStores(
        res.stores.map((s) => ({
          id: s.id,
          store_name: s.store_name,
          address: s.address,
          city: s.city,
          state: s.state,
          zip_code: s.zip_code,
          is_active: true,
          chain_id: s.chain_id,
          chain_name: s.chain_name,
          chain_slug: s.chain_slug,
        })),
      );
      setDealsStatusById(
        Object.fromEntries(res.stores.map((s) => [s.id, s.deals_status])),
      );
      if (!res.stores.length) toast.error("No stores found for that ZIP.");
    } catch {
      toast.error("Couldn't search that ZIP.");
    } finally {
      setDiscovering(false);
    }
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [me, stores, mine] = await Promise.all([
        getMe(),
        getAllStores().catch(() => []),
        getMyStores().catch(() => []),
      ]);
      setForm(me);
      setAllStores(stores);
      setSelected(mine.map((m) => m.store.id));
      setDefaultId(mine.find((m) => m.is_default)?.store.id ?? null);
    } catch {
      toast.error("Couldn't load settings.", load);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  function set<K extends keyof UserProfile>(key: K, value: UserProfile[K]) {
    setForm((f) => (f ? { ...f, [key]: value } : f));
  }

  async function saveProfile() {
    if (!form) return;
    setSavingProfile(true);
    const patch: UserUpdate = {
      name: form.name,
      zip_code: form.zip_code,
      goal: form.goal,
      calorie_target: form.calorie_target,
      protein_target: form.protein_target,
      diet_type: form.diet_type,
      allergies: form.allergies,
      cuisine_preferences: form.cuisine_preferences,
      excluded_ingredients: form.excluded_ingredients,
      skill_level: form.skill_level,
      max_prep_time: form.max_prep_time,
      household_size: form.household_size,
      taste_notes: form.taste_notes,
      recipes_per_generation: form.recipes_per_generation === 3 ? 3 : 5,
    };
    try {
      const updated = await updateMe(patch);
      setForm(updated);
      toast.show({ message: "Profile saved ✓" });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't save profile.");
    } finally {
      setSavingProfile(false);
    }
  }

  function toggleStore(id: number) {
    setSelected((prev) => {
      if (prev.includes(id)) {
        const next = prev.filter((x) => x !== id);
        if (defaultId === id) setDefaultId(next[0] ?? null);
        return next;
      }
      if (prev.length >= MAX_STORES) {
        toast.error(`You can save up to ${MAX_STORES} stores.`);
        return prev;
      }
      if (defaultId === null) setDefaultId(id);
      return [...prev, id];
    });
  }

  async function saveStores() {
    setSavingStores(true);
    try {
      await replaceMyStores(selected, selected.includes(defaultId ?? -1) ? defaultId : (selected[0] ?? null));
      toast.show({ message: "Stores saved ✓" });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't save stores.");
    } finally {
      setSavingStores(false);
    }
  }

  async function signOut() {
    await createClient().auth.signOut();
    router.replace("/login");
    router.refresh();
  }

  if (loading || !form) return <SettingsSkeleton />;

  // group stores by chain
  const byChain = new Map<string, StoreLocation[]>();
  for (const s of allStores) {
    const key = s.chain_name ?? "Other";
    (byChain.get(key) ?? byChain.set(key, []).get(key)!).push(s);
  }

  return (
    <div className="px-5 pt-8 pb-8">
      <h1 className="text-2xl font-bold text-ink">Settings</h1>

      {/* Profile */}
      <Section title="Profile">
        <Field label="Name">
          <input
            value={form.name ?? ""}
            onChange={(e) => set("name", e.target.value)}
            placeholder="Your name"
            className="input"
          />
        </Field>
        <Field label="ZIP code">
          <input
            value={form.zip_code ?? ""}
            onChange={(e) => set("zip_code", e.target.value)}
            placeholder="e.g. 11729"
            inputMode="numeric"
            className="input"
          />
        </Field>
      </Section>

      {/* Goals */}
      <Section title="Goals & preferences">
        <Field label="Goal">
          <select value={form.goal} onChange={(e) => set("goal", e.target.value)} className="input">
            {GOALS.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Calories / day">
            <input
              type="number"
              value={form.calorie_target}
              onChange={(e) => set("calorie_target", Number(e.target.value))}
              className="input"
            />
          </Field>
          <Field label="Protein (g) / day">
            <input
              type="number"
              value={form.protein_target}
              onChange={(e) => set("protein_target", Number(e.target.value))}
              className="input"
            />
          </Field>
        </div>

        <Field label="Diet">
          <select value={form.diet_type} onChange={(e) => set("diet_type", e.target.value)} className="input">
            {DIETS.map((d) => (
              <option key={d} value={d}>{cap(d)}</option>
            ))}
          </select>
        </Field>

        <Field label="Allergies">
          <ChipMultiField
            value={form.allergies}
            onChange={(v) => set("allergies", v)}
            presets={ALLERGY_PRESETS}
            placeholder="Add an allergy"
          />
        </Field>

        <Field label="Cuisines (max 3)">
          <ChipMultiField
            value={form.cuisine_preferences}
            onChange={(v) => set("cuisine_preferences", v)}
            presets={CUISINE_PRESETS}
            max={3}
            placeholder="Add a cuisine"
          />
        </Field>

        <Field label="Never suggest">
          <ChipMultiField
            value={form.excluded_ingredients}
            onChange={(v) => set("excluded_ingredients", v)}
            presets={[]}
            placeholder="Add an ingredient to exclude"
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Skill">
            <select value={form.skill_level} onChange={(e) => set("skill_level", e.target.value)} className="input">
              {SKILLS.map((s) => (
                <option key={s} value={s}>{cap(s)}</option>
              ))}
            </select>
          </Field>
          <Field label="Max prep (min)">
            <input
              type="number"
              value={form.max_prep_time}
              onChange={(e) => set("max_prep_time", Number(e.target.value))}
              className="input"
            />
          </Field>
        </div>

        <Field label="Household size">
          <input
            type="number"
            min={1}
            max={10}
            value={form.household_size}
            onChange={(e) =>
              set("household_size", Math.max(1, Math.min(10, Number(e.target.value) || 1)))
            }
            className="input"
          />
          <span className="text-xs text-ink-faint">How many people each recipe should serve.</span>
        </Field>

        <Field label="Recipes per batch">
          <div className="grid grid-cols-2 gap-2 rounded-2xl border border-hairline bg-surface p-1">
            {([[3, "3 · faster"], [5, "5 · more choice"]] as [3 | 5, string][]).map(([n, label]) => {
              const on = (form.recipes_per_generation === 3 ? 3 : 5) === n;
              return (
                <button
                  key={n}
                  onClick={() => set("recipes_per_generation", n)}
                  aria-pressed={on}
                  className={`h-11 rounded-xl text-sm font-semibold transition ${
                    on ? "bg-brand text-white" : "text-ink-soft"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
          <span className="text-xs text-ink-faint">How many dinner ideas the chef proposes each time.</span>
        </Field>

        <Field label="Tell the chef about your taste">
          <textarea
            value={form.taste_notes ?? ""}
            onChange={(e) => set("taste_notes", e.target.value)}
            placeholder="Anything goes — e.g. loves bold char, smoke, and heat; churrasco energy; hates mushy textures; weeknights = one pan."
            rows={4}
            maxLength={2000}
            className="input min-h-[6rem] resize-y py-2 leading-relaxed"
          />
          <span className="text-xs text-ink-faint">
            The chef reads this every time it builds your recipes.
          </span>
        </Field>

        <button
          onClick={saveProfile}
          disabled={savingProfile}
          className="mt-2 flex h-12 w-full items-center justify-center rounded-2xl bg-brand text-sm font-semibold text-white disabled:opacity-60"
        >
          {savingProfile ? "Saving…" : "Save profile & goals"}
        </button>
      </Section>

      {/* Stores */}
      <Section title="My stores">
        <p className="-mt-1 mb-1 text-xs text-ink-faint">
          Pick up to {MAX_STORES}. Tap the star to set your default.
        </p>
        <div className="mb-1 flex gap-2">
          <input
            value={zip}
            onChange={(e) => setZip(e.target.value.replace(/\D/g, "").slice(0, 5))}
            placeholder="ZIP code"
            inputMode="numeric"
            className="input flex-1"
          />
          <button
            onClick={onDiscover}
            disabled={discovering}
            className="h-12 shrink-0 rounded-2xl bg-brand px-4 text-sm font-semibold text-white disabled:opacity-60"
          >
            {discovering ? "Finding…" : "Find stores"}
          </button>
        </div>
        {[...byChain.entries()].map(([chain, locs]) => (
          <div key={chain} className="rounded-2xl border border-hairline bg-surface">
            <p className="border-b border-hairline px-4 py-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
              {chain}
            </p>
            {locs.map((s, i) => {
              const on = selected.includes(s.id);
              return (
                <div
                  key={s.id}
                  className={`flex items-center gap-3 px-4 py-3 ${i > 0 ? "border-t border-hairline" : ""}`}
                >
                  <button
                    role="checkbox"
                    aria-checked={on}
                    onClick={() => toggleStore(s.id)}
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-2 ${
                      on ? "border-brand bg-brand text-white" : "border-hairline"
                    }`}
                  >
                    {on && <span className="text-xs">✓</span>}
                  </button>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-ink">{s.store_name}</p>
                    {s.city && <p className="truncate text-xs text-ink-faint">{s.city}</p>}
                    {dealsStatusById[s.id] === "pending_source" && (
                      <span className="mt-0.5 inline-block rounded-full bg-warn-soft px-2 py-0.5 text-[10px] font-semibold text-warn">
                        deals coming soon
                      </span>
                    )}
                  </div>
                  <button
                    aria-label="Set default"
                    disabled={!on}
                    onClick={() => setDefaultId(s.id)}
                    className={`shrink-0 text-xl ${
                      defaultId === s.id ? "text-warn" : on ? "text-ink-faint" : "text-hairline"
                    }`}
                  >
                    {defaultId === s.id ? "★" : "☆"}
                  </button>
                </div>
              );
            })}
          </div>
        ))}
        <button
          onClick={saveStores}
          disabled={savingStores}
          className="mt-2 flex h-12 w-full items-center justify-center rounded-2xl bg-brand text-sm font-semibold text-white disabled:opacity-60"
        >
          {savingStores ? "Saving…" : "Save stores"}
        </button>
      </Section>

      {/* Notifications (P41 A): flyer-day push, opt-in, one-tap out. */}
      <Section title="Notifications">
        <PushToggle />
      </Section>

      {/* Account */}
      <Section title="Account">
        <div className="overflow-hidden rounded-2xl border border-hairline bg-surface">
          <Link href="/pantry" className="flex h-14 items-center justify-between px-4 text-base font-medium text-ink active:bg-canvas">
            <span>🧺 Your pantry</span>
            <span className="text-ink-faint">›</span>
          </Link>
          <Link href="/scan" className="flex h-14 items-center justify-between border-t border-hairline px-4 text-base font-medium text-ink active:bg-canvas">
            <span>📸 Scan your kitchen</span>
            <span className="text-ink-faint">›</span>
          </Link>
        </div>
        <button
          onClick={signOut}
          className="mt-3 flex h-12 w-full items-center justify-center rounded-2xl border border-hairline bg-surface text-base font-semibold text-warn active:scale-[.99]"
        >
          Sign out
        </button>
      </Section>
    </div>
  );
}

function PushToggle() {
  const toast = useToast();
  const [supported, setSupported] = useState(true);
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    import("@/lib/pushApi").then(({ isPushSubscribed }) =>
      isPushSubscribed().then((v) => {
        if (!live) return;
        setOn(v);
        setSupported(
          typeof window !== "undefined" &&
            "serviceWorker" in navigator &&
            "PushManager" in window,
        );
      }),
    );
    return () => {
      live = false;
    };
  }, []);

  async function toggle() {
    setBusy(true);
    const { enablePush, disablePush } = await import("@/lib/pushApi");
    try {
      if (on) {
        await disablePush();
        setOn(false);
        toast.show({ message: "Notifications off. We won't ping you again." });
      } else {
        await enablePush();
        setOn(true);
        toast.show({
          message:
            "You'll hear from us when your store's new flyer lands — at most twice a week.",
        });
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't update notifications.");
    } finally {
      setBusy(false);
    }
  }

  if (!supported) {
    return (
      <p className="rounded-2xl border border-hairline bg-surface px-4 py-3 text-sm text-ink-soft">
        This browser doesn&apos;t support notifications.
      </p>
    );
  }

  return (
    <div className="rounded-2xl border border-hairline bg-surface p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-base font-medium text-ink">📰 Notify me when new deals land</p>
          <p className="mt-0.5 text-xs text-ink-soft">
            One heads-up when your store&apos;s weekly flyer flips. Max twice a
            week, nothing in between.
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          disabled={busy}
          onClick={toggle}
          className={`relative h-7 w-12 shrink-0 rounded-full transition ${
            on ? "bg-brand" : "bg-hairline"
          } disabled:opacity-60`}
        >
          <span
            className={`absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition-all ${
              on ? "left-[22px]" : "left-0.5"
            }`}
          />
        </button>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-7">
      <h2 className="mb-3 text-sm font-bold uppercase tracking-wide text-ink-faint">{title}</h2>
      <div className="flex flex-col gap-3">{children}</div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-ink">{label}</span>
      {children}
    </label>
  );
}

function ChipMultiField({
  value,
  onChange,
  presets,
  max,
  placeholder,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  presets: string[];
  max?: number;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const atMax = max != null && value.length >= max;

  function add(raw: string) {
    const v = raw.trim().toLowerCase();
    if (!v || value.includes(v)) return;
    if (atMax) return;
    onChange([...value, v]);
    setText("");
  }
  function remove(v: string) {
    onChange(value.filter((x) => x !== v));
  }

  return (
    <div className="rounded-2xl border border-hairline bg-surface p-3">
      {value.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {value.map((v) => (
            <span
              key={v}
              className="flex items-center gap-1 rounded-full bg-brand-soft px-3 py-1 text-sm font-medium capitalize text-brand-dark"
            >
              {v}
              <button aria-label={`Remove ${v}`} onClick={() => remove(v)} className="text-brand-dark/70">
                ✕
              </button>
            </span>
          ))}
        </div>
      )}

      {!atMax && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            add(text);
          }}
          className="flex gap-2"
        >
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={placeholder}
            className="h-10 flex-1 rounded-xl border border-hairline px-3 text-sm outline-none focus:border-brand"
          />
          <button
            type="submit"
            disabled={!text.trim()}
            className="h-10 rounded-xl bg-brand px-4 text-sm font-semibold text-white disabled:opacity-50"
          >
            Add
          </button>
        </form>
      )}

      {presets.filter((p) => !value.includes(p)).length > 0 && !atMax && (
        <div className="mt-2 flex flex-wrap gap-2">
          {presets
            .filter((p) => !value.includes(p))
            .map((p) => (
              <button
                key={p}
                onClick={() => add(p)}
                className="rounded-full border border-hairline px-3 py-1 text-sm capitalize text-ink-soft"
              >
                + {p}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

function SettingsSkeleton() {
  return (
    <div className="px-5 pt-8">
      <div className="skeleton h-7 w-32 rounded" />
      {[0, 1, 2].map((i) => (
        <div key={i} className="mt-7">
          <div className="skeleton mb-3 h-4 w-24 rounded" />
          <div className="skeleton h-24 w-full rounded-2xl" />
        </div>
      ))}
    </div>
  );
}
