export default function ListPage() {
  return (
    <div className="px-5 pt-8">
      <h1 className="text-2xl font-bold text-ink">Shopping list</h1>
      <p className="mt-1 text-sm text-ink-soft">
        One consolidated list from your week&apos;s recipes, priced against
        current deals.
      </p>
      <div className="mt-6 rounded-2xl border border-dashed border-hairline bg-surface p-6 text-center">
        <p className="text-sm text-ink-soft">Your list will appear here.</p>
      </div>
    </div>
  );
}
