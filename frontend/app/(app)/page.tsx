export default function HomePage() {
  return (
    <div className="px-5 pt-8">
      <header className="mb-6">
        <p className="text-sm text-ink-soft">Welcome back</p>
        <h1 className="text-2xl font-bold text-ink">Tonight&apos;s plan</h1>
      </header>

      {/* Savings highlight — fresh green */}
      <section className="mb-4 rounded-2xl bg-brand p-5 text-white">
        <p className="text-sm/5 opacity-90">This week&apos;s deals at your store</p>
        <p className="mt-1 text-3xl font-bold">Save smart</p>
        <p className="mt-1 text-sm opacity-90">
          Recipes are built around what&apos;s on sale.
        </p>
      </section>

      {/* Use-soon warning — warm amber */}
      <section className="mb-4 flex items-center gap-3 rounded-2xl bg-warn-soft p-4">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-warn/15 text-warn">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M12 9v4" />
            <path d="M12 17h.01" />
            <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
          </svg>
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">Use soon</p>
          <p className="text-sm text-ink-soft">
            Items nearing their prime will show here.
          </p>
        </div>
      </section>

      <section className="rounded-2xl border border-hairline bg-surface p-5">
        <h2 className="text-base font-semibold text-ink">This week</h2>
        <p className="mt-1 text-sm text-ink-soft">
          Saved recipes for the week will appear here. Head to Recipes to
          generate tonight&apos;s options.
        </p>
      </section>
    </div>
  );
}
