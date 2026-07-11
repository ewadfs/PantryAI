export default function ScanPage() {
  return (
    <div className="px-5 pt-8">
      <h1 className="text-2xl font-bold text-ink">Scan your pantry</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Snap a few photos of your fridge and shelves — we&apos;ll detect what
        you have.
      </p>
      <div className="mt-6 flex flex-col items-center gap-4 rounded-2xl border border-dashed border-hairline bg-surface p-8 text-center">
        <span className="flex h-16 w-16 items-center justify-center rounded-full bg-brand-soft text-brand">
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3Z" />
            <circle cx="12" cy="13" r="3.5" />
          </svg>
        </span>
        <p className="text-sm text-ink-soft">Camera capture coming next.</p>
      </div>
    </div>
  );
}
