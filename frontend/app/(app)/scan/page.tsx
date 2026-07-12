"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { compressImage } from "@/lib/image";
import { confirmScan, scanPantry } from "@/lib/pantryApi";
import {
  CATEGORIES,
  categoryLabel,
  guessCategory,
  suggestItems,
} from "@/lib/categories";
import type { ConfirmItem, ScanResponse, UncertainItem } from "@/lib/types";

type Step = "capture" | "analyzing" | "review" | "success";
type Photo = { id: string; file: File; url: string };
type ReviewItem = ConfirmItem & { id: string; freshness: string };
type ReviewUncertain = UncertainItem & { id: string };

const MAX_PHOTOS = 6;

const FUN_FACTS = [
  "The average US household tosses ~$1,500 of food a year. A little planning claws it back.",
  "Roughly a third of all food produced is never eaten.",
  "Storing herbs like flowers — stems in water — can double their fridge life.",
  "Cooking what you already own is the single biggest grocery money-saver.",
  "Freezing bread the day you buy it keeps it bakery-fresh for weeks.",
  "Most \"expired\" pantry staples are fine well past the printed date.",
  "Shopping your own fridge first cuts impulse buys at the store.",
  "A planned week of dinners wastes far less than day-by-day decisions.",
];

let _uid = 0;
const uid = () => `${Date.now()}-${_uid++}`;

export default function ScanPage() {
  const [step, setStep] = useState<Step>("capture");
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [busyAdding, setBusyAdding] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const [scanId, setScanId] = useState<number | null>(null);
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [uncertain, setUncertain] = useState<ReviewUncertain[]>([]);
  const [zoomUrl, setZoomUrl] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedCount, setSavedCount] = useState(0);
  const [mode, setMode] = useState<"replace" | "merge">("merge");

  // When entering review, pre-select mode: remembered explicit choice, else
  // merge for a small (fridge-only) scan, replace for a full-kitchen scan.
  useEffect(() => {
    if (step !== "review") return;
    const saved =
      typeof window !== "undefined" ? localStorage.getItem("pantryai:scanMode") : null;
    if (saved === "replace" || saved === "merge") setMode(saved);
    else setMode(photos.length >= 3 ? "replace" : "merge");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  function chooseMode(m: "replace" | "merge") {
    setMode(m);
    if (typeof window !== "undefined") localStorage.setItem("pantryai:scanMode", m);
  }

  const cameraRef = useRef<HTMLInputElement>(null);
  const galleryRef = useRef<HTMLInputElement>(null);

  // Clean up object URLs on unmount.
  useEffect(() => {
    return () => photos.forEach((p) => URL.revokeObjectURL(p.url));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setError(null);
    setBusyAdding(true);
    const room = MAX_PHOTOS - photosCountRef.current;
    const chosen = Array.from(files).slice(0, Math.max(0, room));
    const next: Photo[] = [];
    for (const f of chosen) {
      try {
        const compressed = await compressImage(f);
        next.push({ id: uid(), file: compressed, url: URL.createObjectURL(compressed) });
      } catch {
        /* skip unreadable file */
      }
    }
    setPhotos((prev) => [...prev, ...next].slice(0, MAX_PHOTOS));
    setBusyAdding(false);
  }, []);

  // keep a ref of the count so addFiles doesn't need photos as a dep
  const photosCountRef = useRef(0);
  useEffect(() => {
    photosCountRef.current = photos.length;
  }, [photos]);

  function removePhoto(id: string) {
    setPhotos((prev) => {
      const gone = prev.find((p) => p.id === id);
      if (gone) URL.revokeObjectURL(gone.url);
      return prev.filter((p) => p.id !== id);
    });
  }

  async function analyze() {
    if (photos.length === 0) return;
    setError(null);
    setProgress(0);
    setStep("analyzing");
    try {
      const res: ScanResponse = await scanPantry(
        photos.map((p) => p.file),
        (frac) => setProgress(frac),
      );
      setScanId(res.scan_id);
      setItems(
        res.items.map((it) => ({
          id: uid(),
          name: it.name,
          quantity_estimate: it.quantity_estimate ?? "",
          unit: it.unit ?? null,
          category: it.category ?? "other",
          is_staple: false,
          freshness: it.freshness ?? "good",
        })),
      );
      // Cards WITH guesses (a real guess) sort first; pure-mystery last.
      const mapped: ReviewUncertain[] = (res.uncertain ?? []).map((u) => ({
        ...u,
        id: uid(),
      }));
      mapped.sort((a, b) => (b.guesses.length > 0 ? 1 : 0) - (a.guesses.length > 0 ? 1 : 0));
      setUncertain(mapped);
      setStep("review");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      setStep("capture");
    }
  }

  function updateItem(id: string, patch: Partial<ReviewItem>) {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)));
  }
  function removeItem(id: string) {
    setItems((prev) => prev.filter((it) => it.id !== id));
  }
  function addItem(name: string, category?: string) {
    const clean = name.trim();
    if (!clean) return;
    setItems((prev) => [
      { id: uid(), name: clean, quantity_estimate: "", unit: null, category: category ?? guessCategory(clean), is_staple: false, freshness: "good" },
      ...prev,
    ]);
  }
  function dismissUncertain(id: string) {
    setUncertain((prev) => prev.filter((u) => u.id !== id));
  }
  function dismissAllUncertain() {
    setUncertain([]);
  }

  async function save() {
    if (scanId == null) return;
    setSaving(true);
    setError(null);
    try {
      const res = await confirmScan(scanId, {
        mode,
        confirmed: items.map(({ id: _id, freshness: _f, ...rest }) => rest),
      });
      setSavedCount(res.active_items);
      if (typeof window !== "undefined") {
        localStorage.setItem("pantryai:lastScanAt", new Date().toISOString());
      }
      setStep("success");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
      setSaving(false);
    }
  }

  const grouped = useMemo(() => groupByCategory(items), [items]);

  return (
    <div className="px-5 pt-8">
      {/* hidden inputs */}
      <input
        ref={cameraRef}
        type="file"
        accept="image/*"
        capture="environment"
        multiple
        className="hidden"
        onChange={(e) => {
          addFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <input
        ref={galleryRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          addFiles(e.target.files);
          e.target.value = "";
        }}
      />

      {step === "capture" && (
        <CaptureStep
          photos={photos}
          busyAdding={busyAdding}
          error={error}
          onCamera={() => cameraRef.current?.click()}
          onGallery={() => galleryRef.current?.click()}
          onRemove={removePhoto}
          onAnalyze={analyze}
        />
      )}

      {step === "analyzing" && <AnalyzingStep count={photos.length} progress={progress} />}

      {step === "review" && (
        <ReviewStep
          grouped={grouped}
          uncertain={uncertain}
          saving={saving}
          error={error}
          onUpdate={updateItem}
          onRemove={removeItem}
          onAdd={addItem}
          onDismissUncertain={dismissUncertain}
          onDismissAllUncertain={dismissAllUncertain}
          onZoom={setZoomUrl}
          onSave={save}
          itemCount={items.length}
          mode={mode}
          onModeChange={chooseMode}
        />
      )}

      {step === "success" && <SuccessStep count={savedCount} />}

      {zoomUrl && (
        <button
          aria-label="Close image"
          onClick={() => setZoomUrl(null)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-6"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={zoomUrl}
            alt="Item close-up"
            className="max-h-[80vh] max-w-full rounded-2xl object-contain"
          />
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Capture                                                            */
/* ------------------------------------------------------------------ */
function CaptureStep({
  photos,
  busyAdding,
  error,
  onCamera,
  onGallery,
  onRemove,
  onAnalyze,
}: {
  photos: Photo[];
  busyAdding: boolean;
  error: string | null;
  onCamera: () => void;
  onGallery: () => void;
  onRemove: (id: string) => void;
  onAnalyze: () => void;
}) {
  const full = photos.length >= MAX_PHOTOS;
  return (
    <div>
      <h1 className="text-2xl font-bold text-ink">Scan your kitchen</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Fridge, pantry, freezer — snap up to {MAX_PHOTOS} photos.
      </p>

      {photos.length > 0 && (
        <div className="mt-5 flex gap-3 overflow-x-auto pb-2">
          {photos.map((p) => (
            <div key={p.id} className="relative h-24 w-20 shrink-0">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={p.url}
                alt="pantry photo"
                className="h-24 w-20 rounded-xl object-cover"
              />
              <button
                aria-label="Remove photo"
                onClick={() => onRemove(p.id)}
                className="absolute -right-2 -top-2 flex h-6 w-6 items-center justify-center rounded-full bg-ink text-white shadow"
              >
                <span className="text-sm leading-none">✕</span>
              </button>
            </div>
          ))}
        </div>
      )}

      <button
        onClick={onCamera}
        disabled={full}
        className="mt-6 flex h-40 w-full flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed border-brand/40 bg-brand-soft text-brand transition active:scale-[.99] disabled:opacity-50"
      >
        <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3Z" />
          <circle cx="12" cy="13" r="3.5" />
        </svg>
        <span className="text-base font-semibold">
          {full ? "Max photos added" : "Take a photo"}
        </span>
      </button>

      <button
        onClick={onGallery}
        disabled={full}
        className="mt-3 flex h-12 w-full items-center justify-center gap-2 rounded-2xl border border-hairline bg-surface text-sm font-medium text-ink transition active:scale-[.99] disabled:opacity-50"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <circle cx="9" cy="9" r="2" />
          <path d="m21 15-4.5-4.5L5 21" />
        </svg>
        Choose from gallery
      </button>

      {busyAdding && (
        <p className="mt-3 text-center text-sm text-ink-soft">Preparing photos…</p>
      )}
      {error && (
        <p className="mt-3 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">
          {error}
        </p>
      )}

      <div className="h-24" />
      <div className="fixed inset-x-0 bottom-16 z-30 mx-auto max-w-md px-5 pb-3">
        <button
          onClick={onAnalyze}
          disabled={photos.length === 0}
          className="flex h-14 w-full items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white shadow-lg shadow-brand/25 transition active:scale-[.99] disabled:opacity-50"
        >
          {photos.length === 0
            ? "Add a photo to start"
            : `Analyze my kitchen (${photos.length} photo${photos.length > 1 ? "s" : ""})`}
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Analyzing                                                          */
/* ------------------------------------------------------------------ */
function AnalyzingStep({ count, progress }: { count: number; progress: number }) {
  const [fact, setFact] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setFact((f) => (f + 1) % FUN_FACTS.length), 3500);
    return () => clearInterval(t);
  }, []);
  const uploaded = progress >= 0.999;
  const pct = Math.round(progress * 100);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center text-center">
      <div className="relative flex h-20 w-20 items-center justify-center">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-soft opacity-70" />
        <span className="relative flex h-16 w-16 items-center justify-center rounded-full bg-brand text-white">
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3Z" />
            <circle cx="12" cy="13" r="3.5" />
          </svg>
        </span>
      </div>

      <h2 className="mt-6 text-lg font-semibold text-ink">
        {uploaded ? "Analyzing your kitchen…" : `Uploading ${count} photo${count > 1 ? "s" : ""}…`}
      </h2>

      <div className="mt-4 h-2 w-56 overflow-hidden rounded-full bg-hairline">
        <div
          className="h-full rounded-full bg-brand transition-all"
          style={{ width: uploaded ? "100%" : `${Math.max(6, pct)}%` }}
        />
      </div>
      <p className="mt-1 text-xs text-ink-faint">
        {uploaded ? "Reading labels and shelves" : `${pct}%`}
      </p>

      <p className="mt-10 max-w-xs text-sm text-ink-soft">💡 {FUN_FACTS[fact]}</p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Review                                                             */
/* ------------------------------------------------------------------ */
function ReviewStep({
  grouped,
  uncertain,
  saving,
  error,
  onUpdate,
  onRemove,
  onAdd,
  onDismissUncertain,
  onDismissAllUncertain,
  onZoom,
  onSave,
  itemCount,
  mode,
  onModeChange,
}: {
  grouped: [string, ReviewItem[]][];
  uncertain: ReviewUncertain[];
  saving: boolean;
  error: string | null;
  onUpdate: (id: string, patch: Partial<ReviewItem>) => void;
  onRemove: (id: string) => void;
  onAdd: (name: string, category?: string) => void;
  onDismissUncertain: (id: string) => void;
  onDismissAllUncertain: () => void;
  onZoom: (url: string) => void;
  onSave: () => void;
  itemCount: number;
  mode: "replace" | "merge";
  onModeChange: (m: "replace" | "merge") => void;
}) {
  const [uncertainOpen, setUncertainOpen] = useState(false);
  return (
    <div className="pb-28">
      <h1 className="text-2xl font-bold text-ink">Review your kitchen</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Tap any name or amount to edit. Remove anything that isn&apos;t right.
      </p>

      <AddItem onAdd={onAdd} />

      {grouped.map(([cat, rows]) => (
        <section key={cat} className="mt-5">
          <h2 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
            {categoryLabel(cat)}
          </h2>
          <div className="overflow-hidden rounded-2xl border border-hairline bg-surface">
            {rows.map((it, i) => (
              <ReviewRow
                key={it.id}
                item={it}
                first={i === 0}
                onUpdate={onUpdate}
                onRemove={onRemove}
              />
            ))}
          </div>
        </section>
      ))}

      {itemCount === 0 && (
        <p className="mt-6 rounded-2xl border border-dashed border-hairline bg-surface p-6 text-center text-sm text-ink-soft">
          No items yet — add what you have above.
        </p>
      )}

      {uncertain.length > 0 && (
        <section className="mt-6">
          <button
            onClick={() => setUncertainOpen((v) => !v)}
            className="flex w-full items-center justify-between rounded-2xl border border-hairline bg-surface px-4 py-3 text-left active:scale-[.99]"
          >
            <span className="text-sm font-medium text-ink">
              🔍 {uncertain.length} thing{uncertain.length === 1 ? "" : "s"} we couldn&apos;t
              identify — review <span className="text-ink-faint">(optional)</span>
            </span>
            <span className={`text-ink-faint transition ${uncertainOpen ? "rotate-180" : ""}`}>
              ▾
            </span>
          </button>

          {uncertainOpen && (
            <div className="mt-2 flex flex-col gap-2">
              {uncertain.map((u) => (
                <UncertainCard
                  key={u.id}
                  item={u}
                  onPick={(name) => {
                    onAdd(name);
                    onDismissUncertain(u.id);
                  }}
                  onDismiss={() => onDismissUncertain(u.id)}
                  onZoom={onZoom}
                />
              ))}
              <button
                onClick={onDismissAllUncertain}
                className="mt-1 self-center text-sm font-medium text-ink-soft underline underline-offset-2"
              >
                Dismiss all remaining
              </button>
            </div>
          )}
        </section>
      )}

      {error && (
        <p className="mt-4 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">
          {error}
        </p>
      )}

      {/* replace vs merge */}
      <div className="mt-6 flex flex-col gap-2">
        <ModeOption
          active={mode === "merge"}
          onClick={() => onModeChange("merge")}
          icon="➕"
          title="Just adding / updating"
          body="Keep everything else in my pantry."
        />
        <ModeOption
          active={mode === "replace"}
          onClick={() => onModeChange("replace")}
          icon="🔄"
          title="This was my whole kitchen"
          body="Replace inventory — drop items not seen here."
        />
      </div>

      <div className="fixed inset-x-0 bottom-16 z-30 mx-auto max-w-md border-t border-hairline bg-canvas/95 px-5 py-3 backdrop-blur">
        <button
          onClick={onSave}
          disabled={saving}
          className="flex h-14 w-full items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white shadow-lg shadow-brand/25 transition active:scale-[.99] disabled:opacity-60"
        >
          {saving
            ? "Saving…"
            : mode === "replace"
              ? "Save — replace my kitchen"
              : "Save — add these items"}
        </button>
      </div>
    </div>
  );
}

function ReviewRow({
  item,
  first,
  onUpdate,
  onRemove,
}: {
  item: ReviewItem;
  first: boolean;
  onUpdate: (id: string, patch: Partial<ReviewItem>) => void;
  onRemove: (id: string) => void;
}) {
  const [editing, setEditing] = useState<null | "name" | "qty">(null);
  return (
    <div className={`flex items-center gap-3 px-4 py-3 ${first ? "" : "border-t border-hairline"}`}>
      <div className="min-w-0 flex-1">
        {editing === "name" ? (
          <input
            autoFocus
            defaultValue={item.name}
            onBlur={(e) => {
              onUpdate(item.id, { name: e.target.value.trim() || item.name });
              setEditing(null);
            }}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            className="w-full rounded-lg border border-brand/40 px-2 py-1 text-base text-ink outline-none"
          />
        ) : (
          <button
            onClick={() => setEditing("name")}
            className="flex items-center gap-2 text-left text-base font-medium text-ink"
          >
            <span className="truncate">{item.name}</span>
            {item.freshness === "use_soon" && (
              <span className="shrink-0 rounded-full bg-warn-soft px-2 py-0.5 text-[11px] font-semibold text-warn">
                use soon
              </span>
            )}
          </button>
        )}

        {editing === "qty" ? (
          <input
            autoFocus
            defaultValue={item.quantity_estimate ?? ""}
            placeholder="quantity"
            onBlur={(e) => {
              onUpdate(item.id, { quantity_estimate: e.target.value.trim() });
              setEditing(null);
            }}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            className="mt-1 w-32 rounded-lg border border-brand/40 px-2 py-1 text-sm text-ink outline-none"
          />
        ) : (
          <button
            onClick={() => setEditing("qty")}
            className="mt-0.5 text-sm text-ink-soft"
          >
            {item.quantity_estimate
              ? `${item.quantity_estimate}${item.unit ? " " + item.unit : ""}`
              : "add amount"}
          </button>
        )}
      </div>

      <button
        aria-label={`Remove ${item.name}`}
        onClick={() => onRemove(item.id)}
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-ink-faint transition active:scale-90"
      >
        <span className="text-lg leading-none">✕</span>
      </button>
    </div>
  );
}

function UncertainCard({
  item,
  onPick,
  onDismiss,
  onZoom,
}: {
  item: ReviewUncertain;
  onPick: (name: string) => void;
  onDismiss: () => void;
  onZoom: (url: string) => void;
}) {
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  return (
    <div className="rounded-2xl border border-hairline bg-surface p-4">
      {item.crop_url && (
        <button
          onClick={() => onZoom(item.crop_url!)}
          className="mb-3 block w-full overflow-hidden rounded-xl bg-canvas active:scale-[.99]"
          aria-label="Enlarge photo"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={item.crop_url}
            alt={item.description}
            className="h-24 w-full object-cover"
          />
        </button>
      )}
      <p className="text-sm text-ink-soft">
        {item.description}
        {item.full_photo && (
          <span className="ml-1 text-xs text-ink-faint">(full photo)</span>
        )}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {item.guesses.map((c) => (
          <button
            key={c}
            onClick={() => onPick(c)}
            className="rounded-full bg-brand-soft px-3 py-1.5 text-sm font-medium text-brand-dark"
          >
            + {c}
          </button>
        ))}
        {!typing && (
          <button
            onClick={() => setTyping(true)}
            className="rounded-full border border-hairline px-3 py-1.5 text-sm font-medium text-ink-soft"
          >
            Type it
          </button>
        )}
        <button
          onClick={onDismiss}
          className="rounded-full border border-hairline px-3 py-1.5 text-sm font-medium text-ink-soft"
        >
          Remove
        </button>
      </div>
      {typing && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) onPick(text);
          }}
          className="mt-3 flex gap-2"
        >
          <input
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="What is it?"
            className="h-10 flex-1 rounded-xl border border-hairline px-3 text-sm outline-none focus:border-brand"
          />
          <button
            type="submit"
            className="h-10 rounded-xl bg-brand px-4 text-sm font-semibold text-white"
          >
            Add
          </button>
        </form>
      )}
    </div>
  );
}

function AddItem({ onAdd }: { onAdd: (name: string, category?: string) => void }) {
  const [text, setText] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  useEffect(() => {
    const t = setTimeout(() => setSuggestions(suggestItems(text)), 180);
    return () => clearTimeout(t);
  }, [text]);

  return (
    <div className="mt-5">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) {
            onAdd(text);
            setText("");
            setSuggestions([]);
          }
        }}
        className="flex gap-2"
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="+ Add item I have"
          className="h-12 flex-1 rounded-2xl border border-hairline bg-surface px-4 text-base outline-none focus:border-brand"
        />
        <button
          type="submit"
          disabled={!text.trim()}
          className="h-12 shrink-0 rounded-2xl bg-brand px-5 text-sm font-semibold text-white disabled:opacity-50"
        >
          Add
        </button>
      </form>
      {suggestions.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {suggestions.map((s) => (
            <button
              key={s}
              onClick={() => {
                onAdd(s);
                setText("");
                setSuggestions([]);
              }}
              className="rounded-full border border-hairline bg-surface px-3 py-1.5 text-sm text-ink-soft"
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Success                                                            */
/* ------------------------------------------------------------------ */
function ModeOption({
  active,
  onClick,
  icon,
  title,
  body,
}: {
  active: boolean;
  onClick: () => void;
  icon: string;
  title: string;
  body: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-start gap-3 rounded-2xl border-2 p-3 text-left transition ${
        active ? "border-brand bg-brand-soft" : "border-hairline bg-surface"
      }`}
    >
      <span className="text-xl" aria-hidden>
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-ink">{title}</span>
        <span className="block text-xs text-ink-soft">{body}</span>
      </span>
      <span
        className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 ${
          active ? "border-brand bg-brand text-white" : "border-hairline"
        }`}
      >
        {active && <span className="text-[10px]">✓</span>}
      </span>
    </button>
  );
}

function SuccessStep({ count }: { count: number }) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center text-center">
      <span className="flex h-20 w-20 items-center justify-center rounded-full bg-brand text-white">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M20 6 9 17l-5-5" />
        </svg>
      </span>
      <h1 className="mt-6 text-2xl font-bold text-ink">Kitchen saved</h1>
      <p className="mt-1 text-sm text-ink-soft">
        {count} item{count === 1 ? "" : "s"} in your pantry. Ready to cook?
      </p>

      <div className="mt-8 flex w-full max-w-xs flex-col gap-3">
        <Link
          href="/recipes?generate=1"
          className="flex h-14 items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white shadow-lg shadow-brand/25 active:scale-[.99]"
        >
          Get recipes →
        </Link>
        <Link
          href="/pantry"
          className="flex h-14 items-center justify-center rounded-2xl border border-hairline bg-surface text-base font-semibold text-ink active:scale-[.99]"
        >
          View pantry
        </Link>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* helpers                                                            */
/* ------------------------------------------------------------------ */
function groupByCategory(items: ReviewItem[]): [string, ReviewItem[]][] {
  const map = new Map<string, ReviewItem[]>();
  for (const it of items) {
    const cat = it.category || "other";
    if (!map.has(cat)) map.set(cat, []);
    map.get(cat)!.push(it);
  }
  const order = [...CATEGORIES];
  return [...map.entries()].sort(
    (a, b) => order.indexOf(a[0] as never) - order.indexOf(b[0] as never),
  );
}
